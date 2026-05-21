# pyright: reportTypedDictNotRequiredAccess=false
"""Unit tests for the three per-site adapter `_parse_review_cards` functions.

The browser-fetch path is mocked at the integration-test level. Here we test
the pure-parsing logic against the synthetic HTML fixtures: each adapter's
`_parse_review_cards` should yield the right ReviewDicts when fed the
fixture HTML, including star-filtering and helpful-count extraction.

The pyright suppression at the top reflects that our adapters always populate
every `NotRequired` ReviewDict field (using `None` rather than omitting); the
TypedDict's NotRequired markers exist for the CSV importer's leniency, not
to encourage sparse output from scraping.
"""

from __future__ import annotations

from pathlib import Path

from apfun.sourcing.review_sites import capterra, g2, trustpilot

_FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "review_sites"


def _load(rel: str) -> str:
    return (_FIXTURE_ROOT / rel).read_text()


def test_g2_parses_three_in_range_filters_fourth() -> None:
    html = _load("g2/asana_page1.html")
    reviews = g2._parse_review_cards(
        html, slug="asana", product_name="Asana", min_star=1, max_star=3
    )
    # Fixture has 4 cards (ratings 2, 3, 1, 4). 1-3 filter keeps three.
    assert len(reviews) == 3
    ids = {r["review_id"] for r in reviews}
    assert "g2-rev-positive" not in ids, "4-star card should be filtered out"
    ratings = sorted(r["rating"] for r in reviews)
    assert ratings == [1, 2, 3]

    # Helpful count, author, permalink, title all populated.
    by_id = {r["review_id"]: r for r in reviews}
    rev1 = by_id["g2-rev-001"]
    assert rev1["rating"] == 2
    assert rev1["site"] == "g2"
    assert rev1["product_slug"] == "asana"
    assert rev1["product_name"] == "Asana"
    assert rev1["helpful_count"] == 23
    assert rev1["author"] == "Sarah K."
    assert rev1["title"] is not None and "Onboarding" in rev1["title"]
    assert rev1["body"].startswith("Took our PMs")
    assert rev1["posted_at"] == "2026-04-12T10:00:00Z"
    permalink = rev1["permalink"]
    assert isinstance(permalink, str) and permalink.startswith("https://www.g2.com/")


def test_capterra_parses_two_filters_5star() -> None:
    html = _load("capterra/asana_page1.html")
    reviews = capterra._parse_review_cards(
        html, slug="asana", product_name="Asana", min_star=1, max_star=3
    )
    # Fixture has ratings 2, 3, 5. 1-3 filter keeps two.
    assert len(reviews) == 2
    ratings = sorted(r["rating"] for r in reviews)
    assert ratings == [2, 3]
    by_id = {r["review_id"]: r for r in reviews}
    assert by_id["cptr-rev-101"]["site"] == "capterra"
    assert by_id["cptr-rev-101"]["helpful_count"] == 18
    assert by_id["cptr-rev-102"]["body"].startswith("Half of what works")


def test_trustpilot_parses_two_filters_5star() -> None:
    html = _load("trustpilot/example_page1.html")
    reviews = trustpilot._parse_review_cards(
        html, slug="example.com", product_name="Example", min_star=1, max_star=3
    )
    # Fixture has ratings 2, 1, 5. 1-3 filter keeps two.
    assert len(reviews) == 2
    ratings = sorted(r["rating"] for r in reviews)
    assert ratings == [1, 2]
    by_id = {r["review_id"]: r for r in reviews}
    rev = by_id["tp-rev-202"]
    assert rev["site"] == "trustpilot"
    assert rev["rating"] == 1
    assert rev["helpful_count"] == 45
    assert rev["body"].startswith("Onboarding flow looked premium")


def test_g2_zero_to_zero_filter_returns_nothing() -> None:
    """Star-range that excludes everything yields an empty list, not an error."""
    html = _load("g2/asana_page1.html")
    reviews = g2._parse_review_cards(
        html, slug="asana", product_name="Asana", min_star=5, max_star=5
    )
    assert reviews == []


def test_g2_handles_missing_helpful_count() -> None:
    """An adapter run against minimal HTML (no helpful-count span) shouldn't crash."""
    html = """
    <article class="review-card" data-review-id="x">
      <h3 class="review-title">t</h3>
      <div class="review-body">b</div>
      <span class="review-rating" data-rating="1"></span>
      <span class="review-author">a</span>
      <time datetime="2026-01-01T00:00:00Z">t</time>
    </article>
    """
    reviews = g2._parse_review_cards(html, slug="x", product_name="X", min_star=1, max_star=5)
    assert len(reviews) == 1
    assert reviews[0]["helpful_count"] is None
    assert reviews[0]["permalink"] is None
