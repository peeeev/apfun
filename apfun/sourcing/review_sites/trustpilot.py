"""Trustpilot review adapter.

Trustpilot paginates reviews at `https://www.trustpilot.com/review/<slug>`
with `?stars=1&stars=2&stars=3&page=N` (repeated `stars` query parameters).
Same Playwright + selectolax pipeline; different selectors.

The fixture (`tests/fixtures/review_sites/trustpilot/`) pins:
`article.tp-review` per review with `data-review-id`, `.tp-review-title`,
`.tp-review-body`, `.tp-rating[data-rating="N"]`, `.tp-reviewer`,
`time[datetime]`. Trustpilot uses "helpful" votes via thumbs-up; the count
lives at `.tp-helpful-count`.
"""

from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from selectolax.parser import HTMLParser

from apfun.sourcing.review_sites._common import (
    AdapterFetchError,
    ReviewDict,
    raise_if_blocked,
)

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext

_REVIEW_URL_TEMPLATE = "https://www.trustpilot.com/review/{slug}"
_PAGE_JITTER_RANGE_S = (1.0, 3.0)


def fetch_reviews(
    context: BrowserContext,
    product: dict[str, Any],
    *,
    max_pages: int,
    min_star: int,
    max_star: int,
) -> list[ReviewDict]:
    slug = product.get("slug")
    if not isinstance(slug, str) or not slug:
        return []
    product_name_raw = product.get("name")
    product_name = product_name_raw if isinstance(product_name_raw, str) else slug

    base_url = _REVIEW_URL_TEMPLATE.format(slug=slug)
    # Trustpilot uses repeated `stars` query params, not a CSV list.
    star_params: list[tuple[str, str]] = [("stars", str(s)) for s in range(min_star, max_star + 1)]

    reviews: list[ReviewDict] = []
    page = context.new_page()
    try:
        for page_num in range(1, max_pages + 1):
            params = star_params + [("page", str(page_num))]
            url = f"{base_url}?{urlencode(params)}"
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
                break
            reviews.extend(page_reviews)
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
    parser = HTMLParser(html)
    out: list[ReviewDict] = []
    for card in parser.css("article.tp-review"):
        rid_attr = card.attributes.get("data-review-id")
        review_id = rid_attr if isinstance(rid_attr, str) and rid_attr else None

        rating_node = card.css_first(".tp-rating")
        rating_attr = rating_node.attributes.get("data-rating") if rating_node else None
        try:
            rating = int(rating_attr) if isinstance(rating_attr, str) else 0
        except ValueError:
            rating = 0
        if rating < min_star or rating > max_star:
            continue

        title_node = card.css_first(".tp-review-title")
        body_node = card.css_first(".tp-review-body")
        author_node = card.css_first(".tp-reviewer")
        time_node = card.css_first("time")
        helpful_node = card.css_first(".tp-helpful-count")
        link_node = card.css_first("a.tp-permalink")

        helpful_count: int | None = None
        if helpful_node and helpful_node.text():
            try:
                helpful_count = int(helpful_node.text(strip=True))
            except ValueError:
                helpful_count = None

        posted_at_attr = time_node.attributes.get("datetime") if time_node else None
        permalink_href = link_node.attributes.get("href") if link_node else None
        permalink: str | None
        if isinstance(permalink_href, str):
            permalink = (
                permalink_href
                if permalink_href.startswith("http")
                else f"https://www.trustpilot.com{permalink_href}"
            )
        else:
            permalink = None

        out.append(
            {
                "site": "trustpilot",
                "product_slug": slug,
                "product_name": product_name,
                "review_id": review_id,
                "title": title_node.text(strip=True) if title_node else None,
                "body": body_node.text(strip=True) if body_node else "",
                "rating": rating,
                "author": author_node.text(strip=True) if author_node else None,
                "posted_at": posted_at_attr if isinstance(posted_at_attr, str) else None,
                "helpful_count": helpful_count,
                "permalink": permalink,
            }
        )
    return out


__all__ = ["fetch_reviews"]
