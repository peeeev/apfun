"""Hacker News ingester (Algolia search API).

Mirrors the structure of `apfun.sourcing.reddit`: per-source `ingest()` returns
an `IngestResult`; batch-aware `ingest_batch()` manages `consecutive_failures`
and writes the `scheduler_runs` row.

HN-specific differences vs Reddit:
- No fail-loud env var. The Algolia API is unauthenticated and Algolia/HN
  doesn't UA-block the way Reddit does — a static USER_AGENT is fine.
- No UA-block batch guard. The Reddit `_UA_BLOCK_BATCH_FRACTION` heuristic
  exists because Reddit silently blocks malformed UAs; that failure mode
  doesn't apply here.
- Filter by points threshold to reduce noise (per task 006 spec).

Code structure deliberately mirrors `reddit.py` rather than sharing a base
class. Per orchestrator feedback 008, accept duplication across the first two
sources; task 007 (ProductHunt) is the third call site, where the right
abstraction shape becomes clear.
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

from apfun.models import RawSignal, SchedulerRun, Source
from apfun.sourcing._rate_limit import TokenBucket

logger = logging.getLogger(__name__)

# heuristic 2026-05-19 — Algolia HN search API is documented as "be reasonable";
# no published QPM. Community practice is 1-2 req/sec sustained. The endpoint
# is generous in practice; this rate is conservative and provides headroom
# against concurrent batch invocations.
_ALGOLIA_SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"

# heuristic 2026-05-19 — 1.5 req/s sustained, burst 3. Sits inside the
# community-reasonable range without thrashing Algolia. Per-instance (one
# bucket for all HN sources in this process) since the rate limit is
# per-client, not per-source.
_BUCKET = TokenBucket(rate_per_sec=1.5, burst=3)

# verified 2026-05-19 — HN/Algolia doesn't enforce a UA format. Static value
# identifying the client is sufficient and polite.
_USER_AGENT = "apfun-funnel/0.1 (https://apfun.online)"

# verified 2026-05-19 https://en.wikipedia.org/wiki/List_of_HTTP_status_codes
# 4xx responses from Algolia indicate a malformed query (our fault) — fail
# the source so we notice and fix the config rather than burn the rate-limit
# budget retrying. Not strictly "permanently gone" the way Reddit's 403/404/410
# are, but the response code → action mapping is identical.
TERMINAL_STATUSES: frozenset[int] = frozenset({400, 401, 403, 404})

# heuristic 2026-05-19 — balances responsiveness against transient single-day
# failures. Three consecutive terminal-status fetches → auto-disable. Same
# threshold as Reddit; if the two diverge we'll learn that operationally.
_AUTO_DISABLE_THRESHOLD = 3

# heuristic 2026-05-19 — three retries with exponential backoff for transient
# HTTP errors. Inline rather than shared with reddit.py; the right abstraction
# emerges with task 007 as the third call site (per feedback 008).
_MAX_RETRIES = 3
_RETRY_BASE_DELAY_S = 1.0

# heuristic 2026-05-19 — defaults per task 006 spec: stories with points<3
# and comments with points<1 are too low-signal to be worth ingesting.
# Configurable per source via `min_story_points` / `min_comment_points`.
_DEFAULT_MIN_STORY_POINTS = 3
_DEFAULT_MIN_COMMENT_POINTS = 1

# verified 2026-05-19 — Algolia's `tags` filter narrows to story-or-comment
# results; without it we get polls and pollopts too. Comma-separated values
# inside parentheses behave as OR.
_DEFAULT_TAGS = "(story,comment)"


@dataclass
class IngestResult:
    """Per-source outcome reported by HN `ingest()` to the batch wrapper.

    Same shape as `apfun.sourcing.reddit.IngestResult` — deliberate duplication
    until task 007 triangulates the right abstraction.
    """

    source_id: int
    items_captured: int
    status_codes: list[int] = field(default_factory=lambda: list[int]())
    error_class: str | None = None
    latency_ms: int = 0


def _content_hash(object_id: str) -> str:
    return hashlib.sha256(object_id.encode("utf-8")).hexdigest()


def _is_comment(hit: dict[str, Any]) -> bool:
    raw_tags = hit.get("_tags")
    if isinstance(raw_tags, list) and "comment" in raw_tags:
        return True
    return bool(hit.get("comment_text"))


def _hit_passes_threshold(
    hit: dict[str, Any], *, min_story_points: int, min_comment_points: int
) -> bool:
    points = hit.get("points")
    if not isinstance(points, int):
        return False
    threshold = min_comment_points if _is_comment(hit) else min_story_points
    return points >= threshold


def _hit_url(hit: dict[str, Any]) -> str:
    """Best URL for a hit — HN item URL when no external `url` was posted."""
    url = hit.get("url")
    if isinstance(url, str) and url:
        return url
    object_id = hit.get("objectID")
    if object_id is not None:
        return f"https://news.ycombinator.com/item?id={object_id}"
    return ""


def _hit_captured_at(hit: dict[str, Any]) -> datetime:
    ts = hit.get("created_at_i")
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(float(ts), tz=UTC)
    return datetime.now(UTC)


def _fetch_search(
    client: httpx.Client, query: str, tags: str, numeric_filters: str | None
) -> tuple[int, dict[str, Any] | None, str | None]:
    """Fetch one Algolia search page. Same retry/terminal semantics as Reddit."""
    params: dict[str, str] = {"query": query, "tags": tags, "hitsPerPage": "50"}
    if numeric_filters:
        params["numericFilters"] = numeric_filters
    last_status = 0
    last_error: str | None = None
    for attempt in range(_MAX_RETRIES):
        _BUCKET.acquire()
        try:
            resp = client.get(
                _ALGOLIA_SEARCH_URL,
                params=params,
                headers={"User-Agent": _USER_AGENT},
                timeout=30.0,
            )
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
    """Capture HN search hits for one source. Returns IngestResult; does NOT mutate counters."""
    started = time.monotonic()
    config = source.config_json or {}
    queries: list[str] = list(config.get("queries", []))
    since_hours: int = int(config.get("since_hours", 24))
    min_story_points: int = int(config.get("min_story_points", _DEFAULT_MIN_STORY_POINTS))
    min_comment_points: int = int(config.get("min_comment_points", _DEFAULT_MIN_COMMENT_POINTS))
    tags: str = config.get("tags", _DEFAULT_TAGS)
    if not queries:
        return IngestResult(source_id=source.id, items_captured=0, latency_ms=0)

    cutoff_ts = int(time.time()) - since_hours * 3600
    numeric_filters = f"created_at_i>{cutoff_ts}"

    owned_client = client is None
    if client is None:
        client = httpx.Client()

    status_codes: list[int] = []
    error_class: str | None = None
    items_captured = 0
    try:
        for query in queries:
            t0 = time.monotonic()
            status, body, err = _fetch_search(client, query, tags, numeric_filters)
            latency_ms = int((time.monotonic() - t0) * 1000)
            status_codes.append(status)
            if err and error_class is None:
                error_class = err
            log_record: dict[str, Any] = {
                "query": query,
                "status_code": status,
                "items_returned": 0 if body is None else len(body.get("hits", [])),
                "latency_ms": latency_ms,
            }
            if err:
                log_record["error_class"] = err
            logger.info("hn.search", extra={"hn": log_record})

            if body is None:
                continue
            for hit in body.get("hits", []):
                if not _hit_passes_threshold(
                    hit,
                    min_story_points=min_story_points,
                    min_comment_points=min_comment_points,
                ):
                    continue
                inserted = _insert_signal(session, source, query, hit)
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


def _insert_signal(session: Session, source: Source, query: str, hit: dict[str, Any]) -> bool:
    """Insert one raw_signal row if its content_hash is novel. Returns True if inserted."""
    object_id = hit.get("objectID")
    if not object_id:
        return False
    object_id_s = str(object_id)
    digest = _content_hash(object_id_s)

    payload: dict[str, Any] = dict(hit)
    payload["_apfun_query"] = query  # which configured query surfaced this hit

    signal = RawSignal(
        source_id=source.id,
        external_id=object_id_s,
        url=_hit_url(hit) or None,
        captured_at=_hit_captured_at(hit),
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
    job_id: str = "hn.ingest_batch",
    client: httpx.Client | None = None,
) -> list[IngestResult]:
    """Run ingest() across sources; manage counter increments + auto-disable.

    No UA-block batch guard (HN doesn't UA-block). Otherwise mirrors Reddit's
    batch wrapper: terminal status increments, transient errors are logged but
    don't increment, three-strikes auto-disables.
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
            except Exception as exc:  # noqa: BLE001 — keep the batch going
                logger.exception("hn.ingest failed for source_id=%s", source.id)
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

        for source, result in zip(sources, results, strict=True):
            _apply_health_update(source, result)

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


def _apply_health_update(source: Source, result: IngestResult) -> None:
    """Update `consecutive_failures` and `is_active` based on the result.

    Rules (same as Reddit's, minus the UA-block branch):
    - Any successful fetch (200 response present in status_codes): reset counter.
    - Any terminal-status (TERMINAL_STATUSES) without a same-source success:
      increment. Counter ≥ threshold → set is_active=False.
    - Transient errors (5xx, 429, timeout, no status_codes at all): leave the
      counter alone.
    """
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
                "hn.source_auto_disabled",
                extra={
                    "hn_auto_disable": {
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
