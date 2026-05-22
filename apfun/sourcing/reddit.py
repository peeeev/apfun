"""Reddit ingester — pulls recent posts from configured subreddits into `raw_signals`.

Shared retry / batch / counter logic lives in `apfun.sourcing._base` (extracted
from this module + hn.py + producthunt.py after three implementations made the
right shape clear). What stays here is Reddit-specific: URL template, content-
hash inputs, capture-but-tag deletion handling, UA-format requirement, and the
UA-block batch guard.

See `docs/tasks/005-reddit-ingester.md` for the full spec.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.orm import Session

from apfun.config import settings
from apfun.db import try_insert
from apfun.models import RawSignal, Source
from apfun.sourcing._base import (
    IngestResult,
    apply_default_health_update,
    run_ingest_batch,
    run_with_retry,
)
from apfun.sourcing._rate_limit import TokenBucket

logger = logging.getLogger(__name__)

# verified 2026-05-19 https://www.reddit.com/r/reddit.com/wiki/api/ — public
# JSON listing endpoints use the `.json` suffix on any URL.
_LISTING_URL_TEMPLATE = "https://www.reddit.com/r/{subreddit}/{kind}.json"

# heuristic 2026-05-19 — Reddit unauth public-JSON ceiling is community-
# reported as ~10 QPM per IP; no authoritative current page. r/redditdev
# threads are the best signal. Our 3.5 req/s sits well under any
# reasonable ceiling; this constant is for sanity-checking, not for
# enforcement.
_REDDIT_UNAUTH_QPM_CEILING = 10

# heuristic 2026-05-19 — Reddit silently degrades non-conformant UAs.
# Format `<platform>:<app>:<version> (by /u/<handle>)` is the community
# convention used by PRAW, snoowrap, and similar libraries. Reddit's own
# API-rules page once spelled this out but has been reorganized away.
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
    """Fetch a single Reddit listing with retry. Returns (status, body, error_class)."""
    url = _LISTING_URL_TEMPLATE.format(subreddit=subreddit, kind=fetch_kind)

    def _request() -> httpx.Response:
        return client.get(url, headers={"User-Agent": _USER_AGENT}, timeout=30.0)

    return run_with_retry(
        _request,
        terminal_statuses=TERMINAL_STATUSES,
        bucket_acquire=_BUCKET.acquire,
    )


def ingest(
    session: Session,
    source: Source,
    client: httpx.Client | None = None,
) -> IngestResult:
    """Capture posts for one Reddit source. Returns IngestResult; does NOT mutate counters."""
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
            log_record: dict[str, Any] = {
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
    return try_insert(session, signal)


def _fraction_403(results: list[IngestResult]) -> float:
    sources_with_status = [r for r in results if r.status_codes]
    if not sources_with_status:
        return 0.0
    saw_403 = sum(1 for r in sources_with_status if 403 in r.status_codes)
    return saw_403 / len(sources_with_status)


def _detect_ua_block(results: list[IngestResult]) -> bool:
    return _fraction_403(results) > _UA_BLOCK_BATCH_FRACTION


def _apply_batch_health_updates(sources: list[Source], results: list[IngestResult]) -> None:
    """Reddit's batch health update: skip increments entirely when UA-block fires."""
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
        return  # don't touch per-source counters under global UA block

    for source, result in zip(sources, results, strict=True):
        apply_default_health_update(
            source,
            result,
            terminal_statuses=TERMINAL_STATUSES,
            threshold=_AUTO_DISABLE_THRESHOLD,
            logger=logger,
            source_kind="reddit",
        )


def ingest_batch(
    session: Session,
    sources: list[Source],
    job_id: str = "reddit.ingest_batch",
    client: httpx.Client | None = None,
) -> list[IngestResult]:
    """Run ingest() across sources; apply UA-block guard + three-strikes auto-disable.

    The outer batch loop, scheduler_runs write, and exception capture live in
    `apfun.sourcing._base.run_ingest_batch`; this function just supplies the
    Reddit-specific per-source `ingest` and the UA-block-aware health updater.
    """
    return run_ingest_batch(
        session,
        sources,
        job_id=job_id,
        client=client,
        ingest_fn=ingest,
        apply_health_updates=_apply_batch_health_updates,
        logger=logger,
    )


__all__ = [
    "IngestResult",
    "TERMINAL_STATUSES",
    "ingest",
    "ingest_batch",
]
