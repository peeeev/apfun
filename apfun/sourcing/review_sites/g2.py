"""G2 review adapter.

Fetches 1-3★ reviews via the public review page (no API). G2 paginates
review pages at `https://www.g2.com/products/<slug>/reviews?filter=1,2,3`
and serves content behind Cloudflare for traffic that looks bot-like.

The adapter:
- Navigates with the existing browser context (Playwright Chromium UA).
- On each page, checks for Cloudflare block markers before parsing
  (raises `_BlockedError` from `_common` if hit).
- Extracts reviews from the rendered DOM via selectolax (faster than
  Playwright's locator API for static post-load HTML).
- Filters by `rating` against `(min_star, max_star)`.

See `docs/tasks/009-review-miner.md` and feedback 014.
"""

from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING, Any

from selectolax.parser import HTMLParser

from apfun.sourcing.review_sites._common import (
    AdapterFetchError,
    ReviewDict,
    raise_if_blocked,
)

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext

# verified 2026-05-19 — G2 uses 1..5 star filter URL params via `filter=1,2,3`
# (CSV); pagination via `page=N`. Reviews land at
# https://www.g2.com/products/<slug>/reviews?filter=1,2,3&page=N
_REVIEW_URL_TEMPLATE = "https://www.g2.com/products/{slug}/reviews"

# heuristic 2026-05-19 — 1-3s per-page jitter, on top of the global 0.5/s
# bucket. Looks less robotic than a regular cadence. Per feedback 014 Q6.
_PAGE_JITTER_RANGE_S = (1.0, 3.0)


def fetch_reviews(
    context: BrowserContext,
    product: dict[str, Any],
    *,
    max_pages: int,
    min_star: int,
    max_star: int,
) -> list[ReviewDict]:
    """Fetch G2 reviews for one product, filtered to the configured star range."""
    slug = product.get("slug")
    if not isinstance(slug, str) or not slug:
        return []
    product_name_raw = product.get("name")
    product_name = product_name_raw if isinstance(product_name_raw, str) else slug

    base_url = _REVIEW_URL_TEMPLATE.format(slug=slug)
    star_filter = ",".join(str(s) for s in range(min_star, max_star + 1))

    reviews: list[ReviewDict] = []
    page = context.new_page()
    try:
        for page_num in range(1, max_pages + 1):
            url = f"{base_url}?filter={star_filter}&page={page_num}"
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            except Exception as exc:  # noqa: BLE001 — surface as adapter fetch error
                raise AdapterFetchError(0, type(exc).__name__) from exc
            status = response.status if response else 0
            if status in {403, 404}:
                raise AdapterFetchError(status, f"HTTP {status}")
            if status and status >= 500:
                raise AdapterFetchError(status, f"HTTP {status}")

            html = page.content()
            raise_if_blocked(html)

            page_reviews = _parse_review_cards(
                html, slug=slug, product_name=product_name, min_star=min_star, max_star=max_star
            )
            if not page_reviews:
                # No more results on this page — stop paginating to avoid
                # walking infinite empty pages.
                break
            reviews.extend(page_reviews)

            # Polite delay between pages (on top of the global TokenBucket).
            time.sleep(random.uniform(*_PAGE_JITTER_RANGE_S))
    finally:
        page.close()
    return reviews


def _parse_review_cards(
    html: str,
    *,
    slug: str,
    product_name: str,
    min_star: int,
    max_star: int,
) -> list[ReviewDict]:
    """Selectolax-based parse of G2 review cards.

    The synthetic fixture (see `tests/fixtures/review_sites/g2/`) pins the
    selector contract: `article.review-card` per review, with `data-review-id`,
    `.review-title`, `.review-body`, `.review-rating[data-rating="N"]`,
    `.review-author`, `time[datetime]`, `.helpful-count`. Real G2 markup is
    more elaborate but these are the load-bearing fields the contract test
    asserts on.
    """
    parser = HTMLParser(html)
    out: list[ReviewDict] = []
    for card in parser.css("article.review-card"):
        rid_attr = card.attributes.get("data-review-id")
        review_id = rid_attr if isinstance(rid_attr, str) and rid_attr else None

        rating_node = card.css_first(".review-rating")
        rating_attr = rating_node.attributes.get("data-rating") if rating_node else None
        try:
            rating = int(rating_attr) if isinstance(rating_attr, str) else 0
        except ValueError:
            rating = 0
        if rating < min_star or rating > max_star:
            continue

        title_node = card.css_first(".review-title")
        body_node = card.css_first(".review-body")
        author_node = card.css_first(".review-author")
        time_node = card.css_first("time")
        helpful_node = card.css_first(".helpful-count")
        link_node = card.css_first("a.review-permalink")

        helpful_count: int | None = None
        if helpful_node and helpful_node.text():
            try:
                helpful_count = int(helpful_node.text(strip=True))
            except ValueError:
                helpful_count = None

        posted_at = (
            time_node.attributes.get("datetime")
            if time_node and isinstance(time_node.attributes.get("datetime"), str)
            else None
        )
        permalink_href = link_node.attributes.get("href") if link_node else None
        permalink: str | None
        if isinstance(permalink_href, str):
            permalink = (
                permalink_href
                if permalink_href.startswith("http")
                else f"https://www.g2.com{permalink_href}"
            )
        else:
            permalink = None

        out.append(
            {
                "site": "g2",
                "product_slug": slug,
                "product_name": product_name,
                "review_id": review_id,
                "title": title_node.text(strip=True) if title_node else None,
                "body": body_node.text(strip=True) if body_node else "",
                "rating": rating,
                "author": author_node.text(strip=True) if author_node else None,
                "posted_at": posted_at if isinstance(posted_at, str) else None,
                "helpful_count": helpful_count,
                "permalink": permalink,
            }
        )
    return out


__all__ = ["fetch_reviews"]
