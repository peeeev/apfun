"""Hacker News ingester (Algolia search API).

Shared retry / batch / counter logic lives in `apfun.sourcing._base`. This
module owns the HN-specific bits: Algolia endpoint, points-threshold
filtering, comment-vs-story detection, and `_apfun_query` payload tagging.

HN-specific differences vs Reddit:
- No fail-loud env var. The Algolia API is unauthenticated and Algolia/HN
  doesn't UA-block the way Reddit does — a static USER_AGENT is fine.
- No UA-block batch guard. The Reddit `_UA_BLOCK_BATCH_FRACTION` heuristic
  exists because Reddit silently blocks malformed UAs; that failure mode
  doesn't apply here.
- Filter by points threshold to reduce noise (per task 006 spec).
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apfun.models import RawSignal, Source
from apfun.sourcing._base import (
    IngestResult,
    apply_default_health_update,
    run_ingest_batch,
    run_with_retry,
)
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

# heuristic 2026-05-19 — defaults per task 006 spec: stories with points<3
# and comments with points<1 are too low-signal to be worth ingesting.
# Configurable per source via `min_story_points` / `min_comment_points`.
_DEFAULT_MIN_STORY_POINTS = 3
_DEFAULT_MIN_COMMENT_POINTS = 1

# verified 2026-05-19 — Algolia's `tags` filter narrows to story-or-comment
# results; without it we get polls and pollopts too. Comma-separated values
# inside parentheses behave as OR.
_DEFAULT_TAGS = "(story,comment)"


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
    """Fetch one Algolia search page. Delegates retry/terminal handling to `_base`."""
    params: dict[str, str] = {"query": query, "tags": tags, "hitsPerPage": "50"}
    if numeric_filters:
        params["numericFilters"] = numeric_filters

    def _request() -> httpx.Response:
        return client.get(
            _ALGOLIA_SEARCH_URL,
            params=params,
            headers={"User-Agent": _USER_AGENT},
            timeout=30.0,
        )

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


def _apply_batch_health_updates(sources: list[Source], results: list[IngestResult]) -> None:
    """HN's batch health update: no special-case branches; pure default rule."""
    for source, result in zip(sources, results, strict=True):
        apply_default_health_update(
            source,
            result,
            terminal_statuses=TERMINAL_STATUSES,
            threshold=_AUTO_DISABLE_THRESHOLD,
            logger=logger,
            source_kind="hn",
        )


def ingest_batch(
    session: Session,
    sources: list[Source],
    job_id: str = "hn.ingest_batch",
    client: httpx.Client | None = None,
) -> list[IngestResult]:
    """Run ingest() across sources; manage counter increments + auto-disable."""
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
