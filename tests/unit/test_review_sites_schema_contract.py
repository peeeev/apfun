"""Contract tests for the review-site fixtures.

The review sites have no documented schema (and active anti-bot posture means
selectors shift more than other sources). Per-site contract asserts the load-
bearing fields each adapter reads. If these fail after a fixture refresh,
selectors changed — investigate before patching the adapter.

See CLAUDE.md → Conventions → "Contract tests for external schemas."
"""

from __future__ import annotations

from pathlib import Path

from selectolax.parser import HTMLParser

_FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "review_sites"


def _load(rel_path: str) -> HTMLParser:
    html = (_FIXTURE_ROOT / rel_path).read_text()
    return HTMLParser(html)


def test_g2_selectors() -> None:
    parser = _load("g2/asana_page1.html")
    cards = parser.css("article.review-card")
    assert len(cards) >= 1, "g2 fixture must have ≥1 review card"
    for card in cards:
        assert card.attributes.get("data-review-id"), "every card needs data-review-id"
        rating_node = card.css_first(".review-rating")
        assert rating_node is not None and rating_node.attributes.get("data-rating"), (
            "every card needs .review-rating[data-rating]"
        )
        assert card.css_first(".review-body") is not None
        assert card.css_first(".review-author") is not None
        assert card.css_first("time") is not None


def test_capterra_selectors() -> None:
    parser = _load("capterra/asana_page1.html")
    cards = parser.css("div.cptr-review")
    assert len(cards) >= 1
    for card in cards:
        assert card.attributes.get("data-review-id")
        rating_node = card.css_first(".cptr-rating")
        assert rating_node is not None and rating_node.attributes.get("data-stars")
        assert card.css_first(".cptr-review-body") is not None
        assert card.css_first(".cptr-reviewer") is not None


def test_trustpilot_selectors() -> None:
    parser = _load("trustpilot/example_page1.html")
    cards = parser.css("article.tp-review")
    assert len(cards) >= 1
    for card in cards:
        assert card.attributes.get("data-review-id")
        rating_node = card.css_first(".tp-rating")
        assert rating_node is not None and rating_node.attributes.get("data-rating")
        assert card.css_first(".tp-review-body") is not None
        assert card.css_first(".tp-reviewer") is not None


def test_fixture_meta_present_in_each() -> None:
    """Every captured fixture starts with _fixture_meta in an HTML comment."""
    for rel in (
        "g2/asana_page1.html",
        "capterra/asana_page1.html",
        "trustpilot/example_page1.html",
    ):
        text = (_FIXTURE_ROOT / rel).read_text()
        assert "_fixture_meta" in text[:500], f"{rel} missing _fixture_meta header"
