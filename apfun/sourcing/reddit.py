"""Reddit ingester — pulls recent posts from configured subreddits into `raw_signals`.

Shared retry / batch / counter logic lives in `apfun.sourcing._base` (extracted
from this module + hn.py + producthunt.py after three implementations made the
right shape clear). What stays here is Reddit-specific: URL template, content-
hash inputs, capture-but-tag deletion handling, UA-format requirement, the
UA-block batch guard, and (per task 005b) the OAuth2 client-credentials flow.

OAuth migration (task 005b, 2026-05-22): we hit `oauth.reddit.com` with a
bearer token from the OAuth token endpoint instead of unauthenticated requests
to `www.reddit.com/r/<sub>/.json`. Datacenter IPs hit a persistent 403 block on
the anonymous path; OAuth is Reddit's official supported path for programmatic
access and is less hostile to datacenter origins.

See `docs/tasks/005-reddit-ingester.md` for the original spec and
`docs/orchestrator/020-reddit-oauth-migration.md` for the OAuth migration spec.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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

# verified 2026-05-22 https://github.com/reddit-archive/reddit/wiki/OAuth2 —
# OAuth2 token endpoint. Token requests go to `www.reddit.com`; listing fetches
# go to `oauth.reddit.com` (next constant).
_REDDIT_OAUTH_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"

# verified 2026-05-22 https://github.com/reddit-archive/reddit/wiki/OAuth2 —
# OAuth API base. All authenticated listing fetches use this host.
_REDDIT_OAUTH_API_BASE = "https://oauth.reddit.com"

# Listing path; combined with `_REDDIT_OAUTH_API_BASE` at fetch time.
_LISTING_PATH_TEMPLATE = "/r/{subreddit}/{kind}.json"

# verified 2026-05-22 https://github.com/reddit-archive/reddit/wiki/API —
# Authenticated OAuth callers are quoted at 100 QPM per OAuth client_id, a
# 10× lift over the unauthenticated ~10 QPM ceiling. Sanity-check constant;
# rate-limiting is still enforced by `_BUCKET` below at 3.5 req/s.
_REDDIT_OAUTH_QPM_CEILING = 100

# heuristic 2026-05-22 — refresh the access token 60 seconds before its
# advertised `expires_in` to avoid handing out a stale token mid-batch. Reddit
# OAuth tokens currently live ~1 hour (3600s); 60s skew is well under that.
REFRESH_SKEW = timedelta(seconds=60)

# heuristic 2026-05-19 — Reddit silently degrades non-conformant UAs.
# Format `<platform>:<app>:<version> (by /u/<handle>)` is the community
# convention used by PRAW, snoowrap, and similar libraries. Reddit's own
# API-rules page once spelled this out but has been reorganized away. The
# format is still required under OAuth (the bearer token doesn't replace UA
# discrimination — both apply).
_USER_AGENT = f"apfun-funnel:v0.1 (by /u/{settings.reddit_username})"

# heuristic 2026-05-19 — community consensus: aim well under the 100 QPM
# OAuth ceiling so spikes from concurrent ingest_batch calls don't trip
# throttling. Burst 5 gives headroom for an initial round-robin across
# active sources.
_BUCKET = TokenBucket(rate_per_sec=3.5, burst=5)

# verified 2026-05-19 https://en.wikipedia.org/wiki/List_of_HTTP_status_codes
# 403 Forbidden, 404 Not Found, 410 Gone — the three "permanently gone"
# signals. Per orchestrator feedback 010 Q2. 401 is *not* in this set — it's
# transient under OAuth (means "refresh the token"), handled inside
# `_fetch_listing` via one retry on a fresh token.
TERMINAL_STATUSES: frozenset[int] = frozenset({403, 404, 410})

# heuristic 2026-05-18 — UA-block detection: if >50% of sources in one batch
# return 403, treat as global block (our UA is malformed or banned), NOT a
# per-source failure. Don't increment per-source counters or auto-disable;
# the issue is our UA, not the subs. Per orchestrator feedback 010 Q2.
#
# Defensive post-OAuth (task 005b): OAuth requests don't produce this pattern
# in practice — datacenter IP blocks land as 401 from `oauth.reddit.com`
# (refreshed away) or 200. Kept as a backstop in case Reddit changes policy
# and the pattern resurfaces. Per orchestrator request 020 §Status-code
# distinction.
_UA_BLOCK_BATCH_FRACTION = 0.5

# heuristic 2026-05-19 — balances responsiveness against transient single-day
# failures. Three consecutive terminal-status fetches → auto-disable.
_AUTO_DISABLE_THRESHOLD = 3


# ───────────── OAuth token lifecycle (task 005b) ─────────────


@dataclass
class _OAuthToken:
    access_token: str
    expires_at: datetime


class _RedditAuth:
    """Owns the OAuth token lifecycle for one process.

    `get_token()` returns a cached token if still valid (with `REFRESH_SKEW`
    headroom) or fetches a fresh one. The lock protects against concurrent
    refresh — currently the batch wrapper runs sequentially across sources,
    but the lock is forward-defensive in case batch fan-out goes parallel.

    Construction validates that credentials are present — empty values raise
    immediately with a CLAUDE.md-pointing message, so loud-failure happens at
    the first `_get_auth()` call (per the auth-secret discipline in CLAUDE.md
    → Auth secret discipline).
    """

    def __init__(self, client_id: str, client_secret: str, user_agent: str) -> None:
        if not client_id or not client_secret:
            raise RuntimeError(
                "Reddit OAuth credentials are missing. Set APFUN_REDDIT_CLIENT_ID "
                "and APFUN_REDDIT_CLIENT_SECRET. Register a 'script' app at "
                "https://www.reddit.com/prefs/apps and copy the credentials. "
                "See CLAUDE.md → Networking and docs/operator/SETUP.md for the "
                "full setup procedure."
            )
        self._client_id = client_id
        self._client_secret = client_secret
        self._user_agent = user_agent
        self._token: _OAuthToken | None = None
        self._lock = threading.Lock()

    def get_token(self, client: httpx.Client, *, force_refresh: bool = False) -> str:
        """Return a valid bearer token, fetching if expired or `force_refresh`.

        Holds the lock across the whole refresh so two threads don't race to
        re-fetch. The fetch itself is short (one POST), so contention is OK.
        """
        with self._lock:
            now = datetime.now(UTC)
            if force_refresh or self._token is None or self._token.expires_at - REFRESH_SKEW <= now:
                self._token = self._fetch_token(client)
            return self._token.access_token

    def invalidate(self) -> None:
        """Drop the cached token so the next `get_token()` re-fetches.

        Used by the listing-fetch path when it sees a 401 (which means the
        token went bad before its advertised expiry — clock skew, server
        revocation, etc.).
        """
        with self._lock:
            self._token = None

    def _fetch_token(self, client: httpx.Client) -> _OAuthToken:
        """POST to the token endpoint with Basic auth + `grant_type=client_credentials`."""
        basic = base64.b64encode(f"{self._client_id}:{self._client_secret}".encode()).decode(
            "ascii"
        )
        # Acquire a rate-limit token even for the auth request — it counts
        # against the per-IP quota.
        _BUCKET.acquire()
        resp = client.post(
            _REDDIT_OAUTH_TOKEN_URL,
            headers={
                "Authorization": f"Basic {basic}",
                "User-Agent": self._user_agent,
            },
            data={"grant_type": "client_credentials"},
            timeout=30.0,
        )
        resp.raise_for_status()
        body: dict[str, Any] = resp.json()
        access = body.get("access_token")
        expires_in = body.get("expires_in")
        if not isinstance(access, str) or not isinstance(expires_in, (int, float)):
            raise RuntimeError(
                f"Reddit token response missing access_token/expires_in: keys={list(body)}"
            )
        return _OAuthToken(
            access_token=access,
            expires_at=datetime.now(UTC) + timedelta(seconds=int(expires_in)),
        )


_auth: _RedditAuth | None = None
_auth_lock = threading.Lock()


def _get_auth() -> _RedditAuth:
    """Lazy-construct the singleton auth object on first use.

    Lazy so that importing this module doesn't fail at startup just because
    OAuth env vars aren't set — the failure surfaces at first ingest call,
    which is the loud-failure call-site shape per CLAUDE.md → Auth secret
    discipline.
    """
    global _auth
    with _auth_lock:
        if _auth is None:
            _auth = _RedditAuth(
                client_id=settings.reddit_client_id,
                client_secret=settings.reddit_client_secret,
                user_agent=_USER_AGENT,
            )
        return _auth


# ───────────── Content hashing + deletion classification (unchanged) ─────────────


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


# ───────────── Listing fetch with OAuth + 401-refresh-retry ─────────────


def _fetch_listing(
    client: httpx.Client, subreddit: str, fetch_kind: str
) -> tuple[int, dict[str, Any] | None, str | None]:
    """Fetch a single Reddit listing with OAuth. Returns (status, body, error_class).

    Token-refresh-retry: a 401 from the API endpoint means the token went bad
    (revoked, clock skew, expired earlier than advertised). We invalidate the
    cached token, fetch a fresh one, and retry the listing fetch once. If the
    second attempt also 401s, that's surfaced (likely bad credentials) and the
    batch wrapper records it without incrementing per-source counters (401 is
    not in `TERMINAL_STATUSES`).

    401 is added to `run_with_retry`'s short-circuit set so we don't burn the
    inner retry budget against a stale token before refreshing.
    """
    auth = _get_auth()
    path = _LISTING_PATH_TEMPLATE.format(subreddit=subreddit, kind=fetch_kind)
    url = f"{_REDDIT_OAUTH_API_BASE}{path}"
    last_status = 0
    last_body: dict[str, Any] | None = None
    last_err: str | None = None
    for token_attempt in range(2):
        token = auth.get_token(client, force_refresh=(token_attempt > 0))

        # Bind token as a default arg so the closure captures the value, not
        # the loop-variable reference (ruff B023). Synchronous call site
        # makes this defensive rather than load-bearing.
        def _request(token: str = token) -> httpx.Response:
            return client.get(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": _USER_AGENT,
                },
                timeout=30.0,
            )

        status, body, err = run_with_retry(
            _request,
            terminal_statuses=TERMINAL_STATUSES | {401},
            bucket_acquire=_BUCKET.acquire,
        )
        last_status, last_body, last_err = status, body, err
        if status == 401 and token_attempt == 0:
            logger.warning(
                "reddit.oauth_401_refresh",
                extra={"subreddit": subreddit, "fetch_kind": fetch_kind},
            )
            auth.invalidate()
            continue
        break
    return last_status, last_body, last_err


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
