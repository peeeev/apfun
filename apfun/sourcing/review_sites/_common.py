"""Shared logic for the G2 / Capterra / Trustpilot review miner.

Per-site adapters expose a single function::

    def fetch_reviews(
        context: BrowserContext,
        product: dict[str, Any],   # {"slug": ..., "name": ...}
        *,
        max_pages: int,
        min_star: int,
        max_star: int,
    ) -> list[ReviewDict]: ...

This module owns the surrounding scaffolding: per-source `ingest()` that
dispatches to the right adapter, dedup via `review_content_hash`, the
batch-aware `ingest_batch()` that uses `BrowserBatchClient` from `_base`,
and the Cloudflare-block-markers list that adapters check before treating a
200 response as a clean fetch (per feedback 014 Q6).

See `docs/tasks/009-review-miner.md` and `docs/orchestrator/014-feedback.md`.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, NotRequired, TypedDict

from sqlalchemy.orm import Session

from apfun.db import try_insert
from apfun.models import RawSignal, SchedulerRun, Source
from apfun.sourcing._base import (
    BrowserBatchClient,
    IngestResult,
    apply_default_health_update,
)
from apfun.sourcing._rate_limit import TokenBucket

logger = logging.getLogger(__name__)

# heuristic 2026-05-19 — 0.5 req/s sustained, burst 2. Conservative across
# review sites (G2/Capterra/Trustpilot) whose anti-bot posture is real and
# unpublished. Combined with per-page jitter inside adapters. Per feedback
# 014 Q6.
_BUCKET = TokenBucket(rate_per_sec=0.5, burst=2)

# heuristic 2026-05-19 — same threshold as the other ingesters. Three
# consecutive terminal-status fetches → auto-disable.
_AUTO_DISABLE_THRESHOLD = 3

# verified 2026-05-19 — review sites use standard HTTP semantics for hard
# blocks; 403 = WAF block, 404 = missing product, 429 = back-pressure (handled
# inside adapters, not terminal).
TERMINAL_STATUSES: frozenset[int] = frozenset({403, 404})

# heuristic 2026-05-19 — empirical markers from observing CF challenge pages
# and rate-limit responses. If a 200 body contains any of these, scraping is
# blocked at the edge; retrying with the same context won't help. Surface in
# scheduler_runs and fall back to manual CSV import (per feedback 014 Q6).
CLOUDFLARE_BLOCK_MARKERS: tuple[str, ...] = (
    "Just a moment",
    "cf-browser-verification",
    "rate limit",
    "Access denied",
    "Attention Required",
)


class ReviewDict(TypedDict):
    """Shape returned by every per-site `fetch_reviews()` adapter.

    Required fields are at the top; the trailing block uses `NotRequired` so
    pyright doesn't flag access on missing keys for the required-by-contract
    portion. Adapters should set every key (using `None` rather than omission)
    even when the underlying review doesn't have one; the NotRequired markers
    exist to accommodate the CSV importer's leniency, not to encourage
    sparse output from scraping.
    """

    site: str
    product_slug: str
    product_name: str
    body: str
    rating: int
    review_id: NotRequired[str | None]
    title: NotRequired[str | None]
    author: NotRequired[str | None]
    posted_at: NotRequired[str | None]
    helpful_count: NotRequired[int | None]
    permalink: NotRequired[str | None]


def review_content_hash(
    site: str,
    product_slug: str,
    review_id: str | None,
    *,
    rating: int | None,
    posted_at: str | None,
    author: str | None,
    body: str | None,
) -> str:
    """Stable hash for a review.

    Prefers the site-issued `review_id` when present. When absent, synthesizes
    an identifier from intrinsic review attributes (per feedback 014 Q5).

    Note: an edited review will produce a new hash, creating a new
    raw_signal row. That's acceptable — edits ARE a kind of new signal —
    and rare enough not to spam the pipeline.
    """
    if review_id:
        return hashlib.sha256(f"{site}|{product_slug}|{review_id}".encode()).hexdigest()
    body_prefix = (body or "").strip()[:200]
    return hashlib.sha256(
        f"{site}|{product_slug}|{rating}|{posted_at}|{author}|{body_prefix}".encode()
    ).hexdigest()


# Adapter signature: (context, product, *, max_pages, min_star, max_star) -> list[ReviewDict]
AdapterFn = Callable[..., list[ReviewDict]]


def _get_adapter(site: str) -> AdapterFn:
    """Lazy-import the per-site adapter so a single broken site doesn't break package import."""
    if site == "g2":
        from apfun.sourcing.review_sites import g2

        return g2.fetch_reviews
    if site == "capterra":
        from apfun.sourcing.review_sites import capterra

        return capterra.fetch_reviews
    if site == "trustpilot":
        from apfun.sourcing.review_sites import trustpilot

        return trustpilot.fetch_reviews
    raise ValueError(f"unknown review site: {site!r}")


def ingest(
    session: Session,
    source: Source,
    client: BrowserBatchClient,
) -> IngestResult:
    """Capture reviews for one review-site source. Returns IngestResult.

    Per feedback 014: opens one BrowserContext for this source (cookies stay
    scoped), iterates configured products, dispatches each to the site's
    adapter, dedup-inserts each review into raw_signals. Does NOT mutate
    counters — `ingest_batch` handles health updates after all per-source
    calls return.
    """
    started = time.monotonic()
    config = source.config_json or {}
    site_raw = config.get("site")
    if not isinstance(site_raw, str):
        return IngestResult(
            source_id=source.id,
            items_captured=0,
            error_class="bad_config_site",
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    site: str = site_raw
    products_raw: Any = config.get("products", [])
    products: list[dict[str, Any]] = (
        [p for p in products_raw if isinstance(p, dict)]  # type: ignore[misc]
        if isinstance(products_raw, list)
        else []
    )
    max_pages: int = int(config.get("max_pages", 3))
    min_star: int = int(config.get("min_star", 1))
    max_star: int = int(config.get("max_star", 3))

    if not products:
        return IngestResult(source_id=source.id, items_captured=0, latency_ms=0)

    try:
        adapter = _get_adapter(site)
    except ValueError as exc:
        logger.error("review_sites.unknown_site", extra={"site": site, "error": str(exc)})
        return IngestResult(
            source_id=source.id,
            items_captured=0,
            error_class="unknown_site",
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    # heuristic 2026-05-19 — review sites soft-block obvious bots more
    # readily than generic Chrome traffic. Don't pass an apfun-identifying
    # UA; Playwright's bundled Chromium UA (recent stable Chrome) is the
    # tactical choice here. Per feedback 014 Q6 — deliberate exception to
    # the self-identifying UA pattern used by Reddit/HN/PH/IH.
    context = client.new_context()
    status_codes: list[int] = []
    error_class: str | None = None
    items_captured = 0
    try:
        for product in products:
            _BUCKET.acquire()
            slug = product.get("slug")
            if not isinstance(slug, str) or not slug:
                continue
            t0 = time.monotonic()
            try:
                reviews = adapter(
                    context,
                    product,
                    max_pages=max_pages,
                    min_star=min_star,
                    max_star=max_star,
                )
            except BlockedError as exc:
                logger.error(
                    "review_sites.blocked",
                    extra={
                        "review_sites_blocked": {
                            "site": site,
                            "product_slug": slug,
                            "marker": exc.marker,
                        }
                    },
                )
                status_codes.append(403)  # treat as terminal for this batch
                if error_class is None:
                    error_class = "blocked"
                continue
            except AdapterFetchError as exc:
                status_codes.append(exc.status_code)
                if error_class is None:
                    error_class = exc.error_class
                continue

            latency_ms = int((time.monotonic() - t0) * 1000)
            status_codes.append(200)
            logger.info(
                "review_sites.product_fetched",
                extra={
                    "review_sites": {
                        "site": site,
                        "product_slug": slug,
                        "reviews": len(reviews),
                        "latency_ms": latency_ms,
                    }
                },
            )
            for review in reviews:
                if _insert_review(session, source, review):
                    items_captured += 1
    finally:
        context.close()

    return IngestResult(
        source_id=source.id,
        items_captured=items_captured,
        status_codes=status_codes,
        error_class=error_class,
        latency_ms=int((time.monotonic() - started) * 1000),
    )


def _insert_review(session: Session, source: Source, review: ReviewDict) -> bool:
    site = review["site"]
    product_slug = review["product_slug"]
    if not site or not product_slug:
        return False
    review_id = review.get("review_id")
    rating = review["rating"]
    posted_at = review.get("posted_at")
    author = review.get("author")
    body: str = review.get("body") or ""
    digest = review_content_hash(
        site,
        product_slug,
        review_id,
        rating=rating,
        posted_at=posted_at,
        author=author,
        body=body,
    )

    permalink = review.get("permalink")
    url: str | None = permalink if permalink else None
    captured_at = (
        datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
        if isinstance(posted_at, str)
        and (posted_at.endswith("Z") or "+" in posted_at or "-" in posted_at[10:])
        else datetime.now(UTC)
    )

    payload: dict[str, Any] = dict(review)
    external_id = review_id if isinstance(review_id, str) and review_id else digest[:32]

    signal = RawSignal(
        source_id=source.id,
        external_id=external_id,
        url=url,
        captured_at=captured_at,
        content_hash=digest,
        payload_json=payload,
    )
    return try_insert(session, signal)


def _apply_batch_health_updates(sources: list[Source], results: list[IngestResult]) -> None:
    """Review-sites batch health update.

    `error_class="bad_config_site"` / `"unknown_site"` are operator config
    issues; they don't increment counters (a bad config value would otherwise
    auto-disable a perfectly reachable source).
    """
    for source, result in zip(sources, results, strict=True):
        if result.error_class in {"bad_config_site", "unknown_site"}:
            continue
        apply_default_health_update(
            source,
            result,
            terminal_statuses=TERMINAL_STATUSES,
            threshold=_AUTO_DISABLE_THRESHOLD,
            logger=logger,
            source_kind="review_sites",
        )


def ingest_batch(
    session: Session,
    sources: list[Source],
    job_id: str = "review_sites.ingest_batch",
    client: BrowserBatchClient | None = None,
) -> list[IngestResult]:
    """Run review-site `ingest()` across sources with one Browser per batch.

    Differs from the httpx-based `run_ingest_batch`: Browser + Playwright
    lifecycle, not a plain `httpx.Client`. The per-source health-update +
    scheduler_runs row logic is otherwise identical to the established shape.
    """
    started_at = datetime.now(UTC)
    owned_client = client is None
    if client is None:
        # Lazy-import so importing this module without playwright installed
        # doesn't blow up at import time. Playwright IS a project dep, but
        # this keeps the import graph clean if someone vendors the
        # package without playwright present.
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        client = BrowserBatchClient(playwright=pw, browser=browser)

    results: list[IngestResult] = []
    batch_error: str | None = None
    try:
        for source in sources:
            try:
                result = ingest(session, source, client)
                results.append(result)
                source.last_fetched_at = datetime.now(UTC)
            except Exception as exc:  # noqa: BLE001 — keep the batch going
                logger.exception("review_sites.ingest failed for source_id=%s", source.id)
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

        _apply_batch_health_updates(sources, results)
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


class AdapterFetchError(Exception):
    """Raised by per-site adapters on terminal/transient fetch failures."""

    def __init__(self, status_code: int, error_class: str):
        super().__init__(f"adapter fetch failed: status={status_code} class={error_class}")
        self.status_code = status_code
        self.error_class = error_class


class BlockedError(Exception):
    """Raised by per-site adapters when a Cloudflare block marker is detected."""

    def __init__(self, marker: str):
        super().__init__(f"blocked by edge: {marker!r}")
        self.marker = marker


def detect_block(html_or_text: str) -> str | None:
    """Return the first matching block marker if `html_or_text` looks like an edge block.

    Adapters call this on each fetched page; on hit they raise `BlockedError`
    via `raise_if_blocked` so `ingest()` can record a single `blocked` outcome
    rather than retry.
    """
    for marker in CLOUDFLARE_BLOCK_MARKERS:
        if marker in html_or_text:
            return marker
    return None


def raise_if_blocked(html_or_text: str) -> None:
    """Convenience: raise `BlockedError` if a block marker is present."""
    marker = detect_block(html_or_text)
    if marker is not None:
        raise BlockedError(marker)
