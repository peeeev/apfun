"""ProductHunt ingester (GraphQL v2 API).

Third call site for the per-source ingester pattern — see orchestrator
feedback 013 Q1. This module intentionally duplicates the shape of
`apfun.sourcing.reddit` and `apfun.sourcing.hn`; the unification refactor PR
follows this task's merge (per feedback 013 action item #4).

ProductHunt-specific differences vs Reddit/HN:
- **GraphQL endpoint** with cursor paging (`posts(first: N, after: <cursor>)`),
  not REST listing. Body is a JSON-encoded query + variables.
- **Token auth via `Authorization: Bearer <token>`**. Client-only token
  (read-only, long-lived) per feedback 013 Q2.
- **Missing-token path is a clean no-op**, not a crash. ProductHunt 401s with a
  loud message when the token is bad, so we fail at the call site (per the
  new "Auth secret discipline" convention) — but for the *missing* case the
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
import random
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apfun.config import settings
from apfun.models import RawSignal, SchedulerRun, Source
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

# heuristic 2026-05-19 — three retries with exponential backoff. Inline rather
# than shared with reddit.py/hn.py; behavior-preserving refactor PR follows this
# task per feedback 013.
_MAX_RETRIES = 3
_RETRY_BASE_DELAY_S = 1.0

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


@dataclass
class IngestResult:
    """Per-source outcome reported by ProductHunt `ingest()` to the batch wrapper.

    Same shape as `apfun.sourcing.reddit.IngestResult` and
    `apfun.sourcing.hn.IngestResult` — deliberate duplication until task 007's
    behavior-preserving refactor unifies them.
    """

    source_id: int
    items_captured: int
    status_codes: list[int] = field(default_factory=lambda: list[int]())
    error_class: str | None = None
    latency_ms: int = 0


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
    """Execute one GraphQL query. Returns (status, body, error_class)."""
    body: dict[str, Any] = {"query": _POSTS_QUERY, "variables": variables}
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    last_status = 0
    last_error: str | None = None
    for attempt in range(_MAX_RETRIES):
        _BUCKET.acquire()
        try:
            resp = client.post(_GRAPHQL_URL, json=body, headers=headers, timeout=30.0)
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
    job_id: str = "producthunt.ingest_batch",
    client: httpx.Client | None = None,
) -> list[IngestResult]:
    """Run ingest() across sources; manage counter increments + auto-disable.

    Missing-token results contribute a clean "ok=True" scheduler_runs row —
    `error_class="missing_token"` does NOT increment per-source counters
    (it's an operator config issue, not a runtime fault of the source).
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
                logger.exception("producthunt.ingest failed for source_id=%s", source.id)
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

    Rules:
    - `error_class="missing_token"`: leave counter alone (operator config issue).
    - Any successful fetch (200 response present in status_codes): reset counter.
    - Any terminal-status (TERMINAL_STATUSES) without a same-source success:
      increment. Counter ≥ threshold → set is_active=False.
    - Transient errors (5xx, 429, timeout, no status_codes at all): leave the
      counter alone.
    """
    if result.error_class == "missing_token":
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
                "producthunt.source_auto_disabled",
                extra={
                    "producthunt_auto_disable": {
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
