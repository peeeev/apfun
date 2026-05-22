"""ProductHunt ingester (GraphQL v2 API).

Shared retry / batch / counter logic lives in `apfun.sourcing._base`. This
module owns the ProductHunt-specific bits: GraphQL POST + cursor paging,
Bearer-token auth, topic fan-out vs leaderboard single-query, vote-count
filtering, and the missing-token clean-no-op path.

ProductHunt-specific differences vs Reddit/HN:
- **GraphQL endpoint** with cursor paging (`posts(first: N, after: <cursor>)`),
  not REST listing. Body is a JSON-encoded query + variables.
- **Token auth via `Authorization: Bearer <token>`**. Client-only token
  (read-only, long-lived) per feedback 013 Q2.
- **Missing-token path is a clean no-op**, not a crash. ProductHunt 401s with a
  loud message when the token is bad, so we fail at the call site (per the
  "Auth secret discipline" convention) — but for the *missing* case the
  ingester returns an empty `IngestResult` with `error_class="missing_token"`
  and lets the batch wrapper write an "ok=True / items_processed=0"
  scheduler_runs row so the scheduler keeps marching while an operator
  notices the warning + zero-row run.
- **Vote-count filter** (per task 007 spec + feedback 013 heads-up) — ProductHunt
  has many low-signal launches. Default 10 votes for topic surface, 5 for
  leaderboard.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, cast

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

# verified 2026-05-19 https://api.producthunt.com/v2/docs — the GraphQL endpoint
# is documented as the v2 API surface; the v1 REST API is sunset/deprecated.
_GRAPHQL_URL = "https://api.producthunt.com/v2/api/graphql"

# heuristic 2026-05-19 — ProductHunt doesn't publish unauth/auth rate limits
# cleanly. Community practice + developer docs suggest "be reasonable"; start
# conservative at 1 req/s sustained, burst 2. Retune after observing 429s.
_BUCKET = TokenBucket(rate_per_sec=1.0, burst=2)

# verified 2026-05-19 — GraphQL APIs follow HTTP status conventions for
# top-level auth/permission errors. 401 = token revoked/invalid; 403 = quota
# exhausted or scope-forbidden; 404 = wrong endpoint path.
TERMINAL_STATUSES: frozenset[int] = frozenset({401, 403, 404})

# heuristic 2026-05-19 — balances responsiveness against transient single-day
# failures. Same threshold as Reddit and HN.
_AUTO_DISABLE_THRESHOLD = 3

# heuristic 2026-05-19 — per feedback 013 heads-up: ProductHunt has many
# low-signal launches. Defaults err high for topic surface (where the long
# tail is real) and lower for leaderboard (where curation already filters).
_DEFAULT_MIN_VOTES_TOPIC = 10
_DEFAULT_MIN_VOTES_LEADERBOARD = 5

# verified 2026-05-19 — the GraphQL `posts` connection accepts `first` and
# `after`; `topic` filters by topic slug; `featuredAfter` filters by ISO-8601
# featured timestamp. Used in both topic and leaderboard surfaces.
_POSTS_QUERY = """
query Posts($first: Int!, $after: String, $featuredAfter: DateTime, $topic: String) {
  posts(first: $first, after: $after, featuredAfter: $featuredAfter, topic: $topic) {
    pageInfo { endCursor hasNextPage }
    edges {
      cursor
      node {
        id
        slug
        name
        tagline
        description
        url
        votesCount
        commentsCount
        featuredAt
        topics { edges { node { name slug } } }
        makers { edges { node { username } } }
      }
    }
  }
}
""".strip()


def _content_hash(slug: str) -> str:
    return hashlib.sha256(slug.encode("utf-8")).hexdigest()


def _iter_post_nodes(body: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield post `node` dicts from a GraphQL response, skipping malformed entries."""
    data = body.get("data")
    if not isinstance(data, dict):
        return
    posts = cast(dict[str, Any], data).get("posts")
    if not isinstance(posts, dict):
        return
    edges = cast(dict[str, Any], posts).get("edges")
    if not isinstance(edges, list):
        return
    for edge in cast(list[Any], edges):
        if not isinstance(edge, dict):
            continue
        node = cast(dict[str, Any], edge).get("node")
        if isinstance(node, dict):
            yield cast(dict[str, Any], node)


def _default_min_votes(surface: str) -> int:
    return _DEFAULT_MIN_VOTES_LEADERBOARD if surface == "leaderboard" else _DEFAULT_MIN_VOTES_TOPIC


def _fetch_posts(
    client: httpx.Client,
    token: str,
    variables: dict[str, Any],
) -> tuple[int, dict[str, Any] | None, str | None]:
    """Execute one GraphQL query. Delegates retry/terminal handling to `_base`."""
    body: dict[str, Any] = {"query": _POSTS_QUERY, "variables": variables}
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    def _request() -> httpx.Response:
        return client.post(_GRAPHQL_URL, json=body, headers=headers, timeout=30.0)

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
    """Capture ProductHunt posts for one source.

    Missing-token path is a clean no-op (per feedback 013 + task 007 spec):
    the ingester logs at WARNING and returns an empty IngestResult tagged
    `error_class="missing_token"`. The batch wrapper turns that into a
    scheduler_runs row with `ok=True / items_processed=0`.
    """
    started = time.monotonic()
    token = settings.producthunt_token
    if not token:
        logger.warning(
            "producthunt.missing_token",
            extra={
                "producthunt_missing_token": {
                    "source_id": source.id,
                    "source_name": source.name,
                    "hint": (
                        "set APFUN_PRODUCTHUNT_TOKEN to a Client-only token; "
                        "see CLAUDE.md → Auth secret discipline"
                    ),
                }
            },
        )
        return IngestResult(
            source_id=source.id,
            items_captured=0,
            error_class="missing_token",
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    config = source.config_json or {}
    surface: str = config.get("surface", "topic")
    topics: list[str] = list(config.get("topics", []))
    n_days: int = int(config.get("n_days", 1))
    min_votes_count: int = int(config.get("min_votes_count", _default_min_votes(surface)))
    page_size: int = int(config.get("page_size", 20))

    featured_after_dt = datetime.now(UTC).replace(microsecond=0)
    featured_after_dt = featured_after_dt.fromtimestamp(
        featured_after_dt.timestamp() - n_days * 86400, tz=UTC
    )
    featured_after = featured_after_dt.isoformat().replace("+00:00", "Z")

    # Topic surface fans out one query per topic; leaderboard surface runs a
    # single un-topic-filtered query (leaderboard ordering comes from the API).
    topic_args: list[str | None] = list(topics) if (surface == "topic" and topics) else [None]

    owned_client = client is None
    if client is None:
        client = httpx.Client()

    status_codes: list[int] = []
    error_class: str | None = None
    items_captured = 0
    try:
        for topic in topic_args:
            variables: dict[str, Any] = {
                "first": page_size,
                "after": None,
                "featuredAfter": featured_after,
                "topic": topic,
            }
            t0 = time.monotonic()
            status, body, err = _fetch_posts(client, token, variables)
            latency_ms = int((time.monotonic() - t0) * 1000)
            status_codes.append(status)
            if err and error_class is None:
                error_class = err
            log_record: dict[str, Any] = {
                "surface": surface,
                "topic": topic,
                "status_code": status,
                "items_returned": 0
                if body is None
                else len(body.get("data", {}).get("posts", {}).get("edges", [])),
                "latency_ms": latency_ms,
            }
            if err:
                log_record["error_class"] = err
            logger.info("producthunt.posts", extra={"producthunt": log_record})

            if body is None:
                continue
            for node in _iter_post_nodes(body):
                votes_count = node.get("votesCount")
                if not isinstance(votes_count, int) or votes_count < min_votes_count:
                    continue
                inserted = _insert_signal(session, source, surface, node)
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


def _insert_signal(session: Session, source: Source, surface: str, node: dict[str, Any]) -> bool:
    """Insert one raw_signal row if its content_hash is novel. Returns True if inserted."""
    slug = node.get("slug")
    if not slug or not isinstance(slug, str):
        return False
    digest = _content_hash(slug)

    payload: dict[str, Any] = dict(node)
    payload["_apfun_surface"] = surface

    featured_at = node.get("featuredAt")
    if isinstance(featured_at, str):
        try:
            captured_at = datetime.fromisoformat(featured_at.replace("Z", "+00:00"))
        except ValueError:
            captured_at = datetime.now(UTC)
    else:
        captured_at = datetime.now(UTC)

    signal = RawSignal(
        source_id=source.id,
        external_id=slug,
        url=node.get("url") or None,
        captured_at=captured_at,
        content_hash=digest,
        payload_json=payload,
    )
    return try_insert(session, signal)


def _apply_batch_health_updates(sources: list[Source], results: list[IngestResult]) -> None:
    """ProductHunt's batch health update: skip the missing-token rows.

    Missing token is an operator config issue, not a runtime fault — the
    scheduler keeps marching while the operator notices the WARNING + zero-row
    `scheduler_runs` row. Per-source counters stay at their current value.
    """
    for source, result in zip(sources, results, strict=True):
        if result.error_class == "missing_token":
            continue
        apply_default_health_update(
            source,
            result,
            terminal_statuses=TERMINAL_STATUSES,
            threshold=_AUTO_DISABLE_THRESHOLD,
            logger=logger,
            source_kind="producthunt",
        )


def ingest_batch(
    session: Session,
    sources: list[Source],
    job_id: str = "producthunt.ingest_batch",
    client: httpx.Client | None = None,
) -> list[IngestResult]:
    """Run ingest() across sources; manage counter increments + auto-disable.

    Missing-token results contribute a clean "ok=True" scheduler_runs row —
    `error_class="missing_token"` does NOT increment per-source counters
    (it's an operator config issue, not a runtime fault).
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
