"""Reddit ingester — pulls recent posts from configured subreddits into `raw_signals`.

Architecture (per orchestrator feedback 011 Q2):
- `ingest(session, source) -> IngestResult` is per-source and dumb. It captures rows,
  observes per-listing HTTP status codes, and reports back. It does NOT mutate
  `source.consecutive_failures` directly.
- `ingest_batch(session, sources) -> list[IngestResult]` is the batch-aware wrapper.
  It tallies status codes across results, applies the UA-block guard, decides per-
  source counter increments + auto-disable, and writes the `scheduler_runs` row.

See `docs/tasks/005-reddit-ingester.md` for the full spec.
"""

from __future__ import annotations

import hashlib
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apfun.config import settings
from apfun.models import RawSignal, SchedulerRun, Source
from apfun.sourcing._rate_limit import TokenBucket

logger = logging.getLogger(__name__)

# verified 2026-05-19 https://www.reddit.com/r/reddit.com/wiki/api/ — public
# JSON listing endpoints use the `.json` suffix on any URL.
_LISTING_URL_TEMPLATE = "https://www.reddit.com/r/{subreddit}/{kind}.json"

# heuristic 2026-05-19 — Reddit's unauth public-JSON ceiling is community-
# reported as ~10 QPM per IP. No authoritative current page found; r/redditdev
# threads are the best signal. Stay well under it.
# TODO verify by end of task 005: Reddit unauth QPM ceiling — official doc URL
# couldn't be sourced this PR; fallback citation is r/redditdev community
# consensus.
_REDDIT_UNAUTH_QPM_CEILING = 10

# heuristic 2026-05-19 — Reddit silently degrades non-conformant User-Agents.
# Format `<platform>:<app>:<version> (by /u/<handle>)` matches the community-
# accepted convention used by PRAW and similar libraries.
# TODO verify by end of task 005: UA format requirement — Reddit's API rules
# page has shifted; fallback citation is PRAW source + r/redditdev threads.
_USER_AGENT = f"apfun-funnel:v0.1 (by /u/{settings.reddit_username})"

# heuristic 2026-05-19 — community consensus: aim well under the 10 QPM
# ceiling so spikes from concurrent ingest_batch calls don't trip throttling.
# Burst 5 gives headroom for an initial round-robin across active sources.
_BUCKET = TokenBucket(rate_per_sec=3.5, burst=5)

# verified 2026-05-19 https://en.wikipedia.org/wiki/List_of_HTTP_status_codes
# 403 Forbidden, 404 Not Found, 410 Gone — the three "permanently gone"
# signals. Per orchestrator feedback 010 Q2.
TERMINAL_STATUSES: frozenset[int] = frozenset({403, 404, 410})

# heuristic 2026-05-18 — UA-block detection: if >50% of sources in one batch
# return 403, treat as global block (our UA is malformed or banned), NOT a
# per-source failure. Don't increment per-source counters or auto-disable;
# the issue is our UA, not the subs. Per orchestrator feedback 010 Q2.
_UA_BLOCK_BATCH_FRACTION = 0.5

# heuristic 2026-05-19 — balances responsiveness against transient single-day
# failures. Three consecutive terminal-status fetches → auto-disable.
_AUTO_DISABLE_THRESHOLD = 3

# heuristic 2026-05-19 — three retries with exponential backoff (1s, 2s, 4s)
# for transient HTTP errors. Implemented inline; will refactor when task 007
# (ProductHunt) is the third call site and the abstraction shape is clear.
_MAX_RETRIES = 3
_RETRY_BASE_DELAY_S = 1.0


@dataclass
class IngestResult:
    """Per-source outcome reported by `ingest()` to the batch wrapper.

    `status_codes` is a list because one source may issue multiple listing
    calls per ingest (multi-subreddit configs). The batch wrapper flattens
    across results to compute the UA-block fraction.
    """

    source_id: int
    items_captured: int
    status_codes: list[int] = field(default_factory=lambda: list[int]())
    error_class: str | None = None
    latency_ms: int = 0


def _content_hash(subreddit: str, external_id: str, title: str, body: str) -> str:
    body_slice = body[:500]
    payload = f"{subreddit}\x00{external_id}\x00{title}\x00{body_slice}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _classify_deletion(body: str) -> tuple[bool, str | None]:
    if body == "[deleted]":
        return True, "[deleted]"
    if body == "[removed]":
        return True, "[removed]"
    return False, None


def _fetch_listing(
    client: httpx.Client, subreddit: str, fetch_kind: str
) -> tuple[int, dict[str, Any] | None, str | None]:
    """Fetch a single listing with retry-on-transient-error. Returns (status, body, error_class).

    On terminal statuses (403/404/410), returns immediately — no retry.
    On 5xx/429/timeout/connect-error, retries up to `_MAX_RETRIES` with
    exponential backoff. Returns the *final* attempt's outcome.
    """
    url = _LISTING_URL_TEMPLATE.format(subreddit=subreddit, kind=fetch_kind)
    last_status = 0
    last_error: str | None = None
    for attempt in range(_MAX_RETRIES):
        _BUCKET.acquire()
        try:
            resp = client.get(url, headers={"User-Agent": _USER_AGENT}, timeout=30.0)
            last_status = resp.status_code
            if resp.status_code in TERMINAL_STATUSES:
                return resp.status_code, None, None
            if 500 <= resp.status_code < 600 or resp.status_code == 429:
                last_error = f"HTTP {resp.status_code}"
            else:
                resp.raise_for_status()
                return resp.status_code, resp.json(), None
        except httpx.HTTPError as exc:
            last_error = type(exc).__name__
        if attempt < _MAX_RETRIES - 1:
            delay = _RETRY_BASE_DELAY_S * (2**attempt) + random.uniform(0, 0.1)
            time.sleep(delay)
    return last_status, None, last_error


def ingest(
    session: Session,
    source: Source,
    client: httpx.Client | None = None,
) -> IngestResult:
    """Capture posts for one Reddit source. Returns an IngestResult; does NOT mutate counters."""
    started = time.monotonic()
    config = source.config_json or {}
    subreddits: list[str] = list(config.get("subreddits", []))
    fetch_kind: str = config.get("fetch_kind", "new")
    if not subreddits:
        return IngestResult(source_id=source.id, items_captured=0, latency_ms=0)

    owned_client = client is None
    if client is None:
        client = httpx.Client()

    status_codes: list[int] = []
    error_class: str | None = None
    items_captured = 0
    try:
        for subreddit in subreddits:
            t0 = time.monotonic()
            status, body, err = _fetch_listing(client, subreddit, fetch_kind)
            latency_ms = int((time.monotonic() - t0) * 1000)
            status_codes.append(status)
            if err and error_class is None:
                error_class = err
            log_record = {
                "subreddit": subreddit,
                "status_code": status,
                "items_returned": 0
                if body is None
                else len(body.get("data", {}).get("children", [])),
                "latency_ms": latency_ms,
            }
            if err:
                log_record["error_class"] = err
            logger.info("reddit.listing", extra={"reddit": log_record})

            if body is None:
                continue
            for child in body.get("data", {}).get("children", []):
                row = child.get("data", {})
                inserted = _insert_signal(session, source, subreddit, row)
                if inserted:
                    items_captured += 1
    finally:
        if owned_client:
            client.close()

    return IngestResult(
        source_id=source.id,
        items_captured=items_captured,
        status_codes=status_codes,
        error_class=error_class,
        latency_ms=int((time.monotonic() - started) * 1000),
    )


def _insert_signal(session: Session, source: Source, subreddit: str, row: dict[str, Any]) -> bool:
    """Insert a raw_signal row if its content_hash is novel. Returns True if inserted."""
    external_id = str(row.get("id", ""))
    title = str(row.get("title", ""))
    body = str(row.get("selftext", ""))
    if not external_id:
        return False

    digest = _content_hash(subreddit, external_id, title, body)
    is_deleted, deletion_marker = _classify_deletion(body)

    payload: dict[str, Any] = dict(row)
    if is_deleted:
        payload["is_deleted"] = True
        payload["deletion_marker"] = deletion_marker

    permalink = row.get("permalink")
    url = f"https://www.reddit.com{permalink}" if permalink else row.get("url")

    created_utc = row.get("created_utc")
    captured_at = (
        datetime.fromtimestamp(float(created_utc), tz=UTC)
        if isinstance(created_utc, (int, float))
        else datetime.now(UTC)
    )

    signal = RawSignal(
        source_id=source.id,
        external_id=f"t3_{external_id}",
        url=url,
        captured_at=captured_at,
        content_hash=digest,
        payload_json=payload,
    )
    session.add(signal)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        return False
    return True


def ingest_batch(
    session: Session,
    sources: list[Source],
    job_id: str = "reddit.ingest_batch",
    client: httpx.Client | None = None,
) -> list[IngestResult]:
    """Run ingest() across sources; apply UA-block guard + three-strikes auto-disable.

    Writes one `scheduler_runs` row summarizing the batch. Per-source counter
    updates happen here, not inside `ingest()` — see orchestrator feedback 011 Q2.
    """
    started_at = datetime.now(UTC)
    owned_client = client is None
    if client is None:
        client = httpx.Client()

    results: list[IngestResult] = []
    batch_error: str | None = None
    try:
        for source in sources:
            try:
                result = ingest(session, source, client=client)
                results.append(result)
                source.last_fetched_at = datetime.now(UTC)
            except Exception as exc:  # noqa: BLE001 — capture so batch keeps going
                logger.exception("reddit.ingest failed for source_id=%s", source.id)
                results.append(
                    IngestResult(
                        source_id=source.id,
                        items_captured=0,
                        status_codes=[],
                        error_class=type(exc).__name__,
                    )
                )
                if batch_error is None:
                    batch_error = type(exc).__name__

        ua_blocked = _detect_ua_block(results)
        if ua_blocked:
            logger.error(
                "reddit.ua_block_detected",
                extra={
                    "reddit_ua_block": {
                        "batch_size": len(results),
                        "fraction_403": _fraction_403(results),
                    }
                },
            )

        for source, result in zip(sources, results, strict=True):
            _apply_health_update(source, result, ua_blocked=ua_blocked)

        session.commit()
    finally:
        if owned_client:
            client.close()

    finished_at = datetime.now(UTC)
    session.add(
        SchedulerRun(
            job_id=job_id,
            started_at=started_at,
            finished_at=finished_at,
            ok=batch_error is None,
            error=batch_error,
            items_processed=sum(r.items_captured for r in results),
        )
    )
    session.commit()
    return results


def _fraction_403(results: list[IngestResult]) -> float:
    sources_with_status = [r for r in results if r.status_codes]
    if not sources_with_status:
        return 0.0
    saw_403 = sum(1 for r in sources_with_status if 403 in r.status_codes)
    return saw_403 / len(sources_with_status)


def _detect_ua_block(results: list[IngestResult]) -> bool:
    return _fraction_403(results) > _UA_BLOCK_BATCH_FRACTION


def _apply_health_update(source: Source, result: IngestResult, *, ua_blocked: bool) -> None:
    """Update `consecutive_failures` and `is_active` based on the result.

    Rules:
    - UA-blocked batch: don't touch per-source counters at all.
    - Any successful fetch (200 response present in status_codes): reset counter.
    - Any terminal-status (403/404/410) without a success in the same source:
      increment. If counter hits `_AUTO_DISABLE_THRESHOLD`, set is_active=False.
    - Transient errors (5xx, 429, timeout, no status_codes at all): leave the
      counter alone — they're about us or Reddit, not about the sub being dead.
    """
    if ua_blocked:
        return
    if not result.status_codes:
        return

    saw_success = any(200 <= s < 300 for s in result.status_codes)
    saw_terminal = any(s in TERMINAL_STATUSES for s in result.status_codes)

    if saw_success:
        source.consecutive_failures = 0
        return
    if saw_terminal:
        source.consecutive_failures += 1
        if source.consecutive_failures >= _AUTO_DISABLE_THRESHOLD:
            source.is_active = False
            logger.warning(
                "reddit.source_auto_disabled",
                extra={
                    "reddit_auto_disable": {
                        "source_id": source.id,
                        "source_name": source.name,
                        "consecutive_failures": source.consecutive_failures,
                        "status_codes": result.status_codes,
                    }
                },
            )


__all__ = [
    "IngestResult",
    "TERMINAL_STATUSES",
    "ingest",
    "ingest_batch",
]
