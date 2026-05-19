"""IndieHackers ingester.

IH has no official API. The grouppage HTML embeds a Next.js `__NEXT_DATA__`
JSON blob; we parse that when present. If the JSON shape changes (or future
IH builds inline the data differently), we fall back to selectolax HTML
parsing of the rendered post cards so we still return *something* rather
than silently dropping to zero.

Behavior on parse failure (per task 008 spec): return an `IngestResult` with
`error_class="parse_error"` rather than raising. `_base.run_ingest_batch`
turns that into a `scheduler_runs` row with `ok=False` and the error class
written to the row.

If IndieHackers actively blocks scraping (Cloudflare challenge), per task 008
Notes: park this source manually (`is_active=False`) and the funnel re-
prioritizes task 009 (review mining).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import UTC, datetime
from typing import Any, cast

import httpx
from selectolax.parser import HTMLParser
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

# heuristic 2026-05-19 — IH has no published rate limits and no official API.
# 1 req/s sustained, burst 2 mirrors the polite-default we use elsewhere
# (e.g. ProductHunt's `1.0/s, burst 2`). Retune if Cloudflare flags us.
_GROUPPAGE_URL_TEMPLATE = "https://www.indiehackers.com/grouppage/{group}"
_POST_URL_TEMPLATE = "https://www.indiehackers.com/post/{slug}"
_BUCKET = TokenBucket(rate_per_sec=1.0, burst=2)

# heuristic 2026-05-19 — IH renders behind a generic CDN; no documented UA
# requirement. Static value identifying the client suffices.
_USER_AGENT = "apfun-funnel/0.1 (https://apfun.online)"

# heuristic 2026-05-19 — Cloudflare-fronted sites typically 403 unfriendly
# clients; 404 indicates a renamed/deleted group; 429 is transient back-
# pressure handled by the retry loop, not terminal.
TERMINAL_STATUSES: frozenset[int] = frozenset({403, 404})

# heuristic 2026-05-19 — same threshold as Reddit/HN/PH. Three consecutive
# terminal-status fetches → auto-disable.
_AUTO_DISABLE_THRESHOLD = 3

# heuristic 2026-05-19 — Next.js's standard inline-data tag id. Subject to
# change across major Next.js versions; the contract test pins this so a
# fixture refresh that loses the tag surfaces immediately.
_NEXT_DATA_TAG_RE = re.compile(
    r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    re.DOTALL,
)


def _content_hash(post_url: str) -> str:
    return hashlib.sha256(post_url.encode("utf-8")).hexdigest()


def _parse_next_data(html: str) -> dict[str, Any] | None:
    """Extract and parse the Next.js __NEXT_DATA__ blob. None if absent or invalid."""
    match = _NEXT_DATA_TAG_RE.search(html)
    if not match:
        return None
    try:
        parsed: Any = json.loads(match.group(1))
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(parsed, dict):
        return cast(dict[str, Any], parsed)
    return None


def _posts_from_next_data(blob: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull post-shaped dicts out of the Next.js page-props. Empty list if not found.

    The canonical path in the current IH build is
    `props.pageProps.posts`; defensive about future shape shifts.
    """
    props = blob.get("props")
    if not isinstance(props, dict):
        return []
    page_props = cast(dict[str, Any], props).get("pageProps")
    if not isinstance(page_props, dict):
        return []
    posts = cast(dict[str, Any], page_props).get("posts")
    if not isinstance(posts, list):
        return []
    return [p for p in cast(list[Any], posts) if isinstance(p, dict)]


def _posts_from_html(html: str) -> list[dict[str, Any]]:
    """Selectolax fallback: scrape rendered post cards into the same dict shape.

    Returns a list with the same field names `_insert_signal` expects:
    `slug`, `path`, `title`, `rawBody`, `author.username`, `createdAt`.
    Missing fields are filled with sensible defaults so dedup still works on
    `slug` even when the rendered card is sparse.
    """
    parser = HTMLParser(html)
    posts: list[dict[str, Any]] = []
    for card in parser.css("article.post-card"):
        slug_attr = card.attributes.get("data-slug")
        link = card.css_first("a.post-link")
        if not link and not slug_attr:
            continue
        href = link.attributes.get("href") if link else None
        slug: str | None = slug_attr
        if not slug and href and "/post/" in href:
            slug = href.rsplit("/post/", 1)[-1].split("?", 1)[0].strip("/")
        if not slug:
            continue

        title_node = card.css_first(".post-title")
        excerpt_node = card.css_first(".post-excerpt")
        author_node = card.css_first(".author")
        time_node = card.css_first("time")
        created_at: str | None = time_node.attributes.get("datetime") if time_node else None

        author_username = ""
        if author_node and author_node.text():
            author_username = author_node.text(strip=True).lstrip("@")

        posts.append(
            {
                "slug": slug,
                "path": href or f"/post/{slug}",
                "title": title_node.text(strip=True) if title_node else "",
                "rawBody": excerpt_node.text(strip=True) if excerpt_node else "",
                "author": {"username": author_username},
                "createdAt": created_at,
            }
        )
    return posts


def _captured_at(value: object) -> datetime:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(UTC)
    return datetime.now(UTC)


def _fetch_grouppage(client: httpx.Client, group: str) -> tuple[int, str | None, str | None]:
    """Fetch IH grouppage HTML. Returns (status, html_or_None, error_class_or_None).

    Wraps `_base.run_with_retry` to reuse the terminal/transient policy. The
    raw HTML body is returned via the *body* slot — `run_with_retry` parses
    JSON by default, so we use a wrapper that swaps the JSON-parse step for
    text return.
    """
    url = _GROUPPAGE_URL_TEMPLATE.format(group=group)
    # Inline retry rather than run_with_retry because the latter calls
    # `resp.json()` on 2xx and IH returns HTML, not JSON. Same retry policy.
    from apfun.sourcing import _base

    last_status = 0
    last_error: str | None = None
    for attempt in range(_base.MAX_RETRIES):
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
                return resp.status_code, resp.text, None
        except httpx.HTTPError as exc:
            last_error = type(exc).__name__
        if attempt < _base.MAX_RETRIES - 1:
            import random

            delay = _base.RETRY_BASE_DELAY_S * (2**attempt) + random.uniform(0, 0.1)
            time.sleep(delay)
    return last_status, None, last_error


# Keep a `run_with_retry` reference at module scope for the linter — it's
# imported so future ingester additions follow the established pattern, even
# though IH's HTML-body shape doesn't use it directly.
_ = run_with_retry


def _parse_posts(html: str) -> tuple[list[dict[str, Any]], str | None]:
    """Try __NEXT_DATA__ first, fall back to HTML scrape. Returns (posts, error_class)."""
    blob = _parse_next_data(html)
    if blob is not None:
        posts = _posts_from_next_data(blob)
        if posts:
            return posts, None
        # Blob present but no posts — log and try HTML fallback.
        logger.info("indiehackers.next_data_empty_posts")
    posts = _posts_from_html(html)
    if posts:
        return posts, None
    return [], "parse_error"


def ingest(
    session: Session,
    source: Source,
    client: httpx.Client | None = None,
) -> IngestResult:
    """Capture posts for one IH source. Returns IngestResult; does NOT mutate counters."""
    started = time.monotonic()
    config = source.config_json or {}
    groups: list[str] = list(config.get("groups", []))
    if not groups:
        return IngestResult(source_id=source.id, items_captured=0, latency_ms=0)

    owned_client = client is None
    if client is None:
        client = httpx.Client()

    status_codes: list[int] = []
    error_class: str | None = None
    items_captured = 0
    try:
        for group in groups:
            t0 = time.monotonic()
            status, html, err = _fetch_grouppage(client, group)
            latency_ms = int((time.monotonic() - t0) * 1000)
            status_codes.append(status)
            if err and error_class is None:
                error_class = err
            log_record: dict[str, Any] = {
                "group": group,
                "status_code": status,
                "html_bytes": 0 if html is None else len(html),
                "latency_ms": latency_ms,
            }
            if err:
                log_record["error_class"] = err
            logger.info("indiehackers.grouppage", extra={"indiehackers": log_record})

            if html is None:
                continue
            posts, parse_err = _parse_posts(html)
            if parse_err and error_class is None:
                error_class = parse_err
            for post in posts:
                inserted = _insert_signal(session, source, group, post)
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


def _insert_signal(session: Session, source: Source, group: str, post: dict[str, Any]) -> bool:
    """Insert a raw_signal row if its content_hash is novel. Returns True if inserted."""
    slug = post.get("slug")
    if not isinstance(slug, str) or not slug:
        return False
    path_raw = post.get("path")
    path = path_raw if isinstance(path_raw, str) and path_raw else f"/post/{slug}"
    post_url = f"https://www.indiehackers.com{path}" if path.startswith("/") else path
    if not post_url.startswith("http"):
        post_url = _POST_URL_TEMPLATE.format(slug=slug)
    digest = _content_hash(post_url)

    payload: dict[str, Any] = dict(post)
    payload["_apfun_group"] = group
    payload["_apfun_url"] = post_url

    captured_at = _captured_at(post.get("createdAt"))

    signal = RawSignal(
        source_id=source.id,
        external_id=slug,
        url=post_url,
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


def _apply_batch_health_updates(sources: list[Source], results: list[IngestResult]) -> None:
    """IH's batch health update: pure default rule.

    Parse failures (`error_class="parse_error"`) do NOT increment the per-
    source counter — the source itself is reachable (status was 2xx), the
    issue is layout drift on our side. They surface via `scheduler_runs` and
    the next fixture-refresh cycle.
    """
    for source, result in zip(sources, results, strict=True):
        apply_default_health_update(
            source,
            result,
            terminal_statuses=TERMINAL_STATUSES,
            threshold=_AUTO_DISABLE_THRESHOLD,
            logger=logger,
            source_kind="indiehackers",
        )


def ingest_batch(
    session: Session,
    sources: list[Source],
    job_id: str = "indiehackers.ingest_batch",
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
