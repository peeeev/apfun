"""Per-source extractor tests.

Tests each `extract_*` function against representative payload shapes drawn
from the existing source fixtures. Verifies:
- combined text shape (title + body) and whitespace normalization
- `social_proof_weight` heuristic math
- `is_low_signal` correctly tags Reddit deletions
- HN comment-vs-story branching
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from apfun.pipeline._extractors import (
    EXTRACTORS,
    ExtractedText,
    extract_hn,
    extract_indiehackers,
    extract_producthunt,
    extract_reddit,
    extract_review_sites,
    get_extractor,
)

_FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures"


def _reddit_payload(*, deleted: bool = False) -> dict[str, Any]:
    return {
        "title": "What billing tool do you wish existed for early-stage SaaS?",
        "selftext": "Stripe is fine for cards but everything else is a mess."
        if not deleted
        else "[deleted]",
        "score": 42,
        "num_comments": 15,
        "is_deleted": deleted,
        "deletion_marker": "[deleted]" if deleted else None,
    }


def test_reddit_combines_title_and_body() -> None:
    out = extract_reddit(_reddit_payload())
    assert "What billing tool" in out.text
    assert "Stripe is fine" in out.text
    assert "\n\n" not in out.text  # whitespace collapsed to single spaces
    assert out.is_low_signal is False


def test_reddit_weight_is_score_plus_two_times_comments() -> None:
    out = extract_reddit(_reddit_payload())
    # score=42, num_comments=15 → 42 + 2*15 = 72
    assert out.social_proof_weight == 72.0


def test_reddit_negative_score_floors_to_zero() -> None:
    payload = _reddit_payload()
    payload["score"] = -10
    payload["num_comments"] = 5
    out = extract_reddit(payload)
    # max(-10, 0) + 2*5 = 10
    assert out.social_proof_weight == 10.0


def test_reddit_deleted_uses_title_only_and_flags_low_signal() -> None:
    out = extract_reddit(_reddit_payload(deleted=True))
    assert out.is_low_signal is True
    assert "What billing tool" in out.text
    assert "[deleted]" not in out.text  # body dropped on deletion


def test_hn_story_combines_title_and_story_text() -> None:
    payload = {
        "title": "Ask HN: What developer tool do you wish existed?",
        "story_text": "I'm sick of cobbling together five services for observability.",
        "points": 64,
        "num_comments": 87,
        "_tags": ["story", "ask_hn"],
    }
    out = extract_hn(payload)
    assert "Ask HN" in out.text
    assert "cobbling together" in out.text
    # points=64, num_comments=87 → 64 + 2*87 = 238
    assert out.social_proof_weight == 238.0
    assert out.is_low_signal is False


def test_hn_comment_uses_comment_text_only() -> None:
    payload = {
        "title": None,
        "comment_text": "I wish there were a self-hosted alternative to Linear.",
        "points": 4,
        "_tags": ["comment", "story_44000001"],
    }
    out = extract_hn(payload)
    assert "self-hosted alternative" in out.text
    # No title polluting the text.
    assert "None" not in out.text


def test_producthunt_combines_three_fields() -> None:
    payload = {
        "name": "Tiny Observability",
        "tagline": "One-binary observability stack for indie SaaS",
        "description": "Drop-in metrics + traces + logs for a single host.",
        "votesCount": 245,
        "commentsCount": 31,
    }
    out = extract_producthunt(payload)
    assert "Tiny Observability" in out.text
    assert "One-binary observability stack" in out.text
    assert "Drop-in metrics" in out.text
    assert out.social_proof_weight == 245.0  # votesCount only


def test_indiehackers_combines_title_and_body() -> None:
    payload = {
        "slug": "bootstrapping-a-niche-saas-to-3k-mrr",
        "title": "Bootstrapping a niche SaaS to $3k MRR in 6 months",
        "rawBody": "Solo bootstrapper here. Sharing the unglamorous middle.",
        "upvoteCount": 47,
        "replyCount": 12,
    }
    out = extract_indiehackers(payload)
    assert "Bootstrapping a niche SaaS" in out.text
    assert "Solo bootstrapper" in out.text
    # upvotes=47, replies=12 → 47 + 2*12 = 71
    assert out.social_proof_weight == 71.0


def test_review_sites_uses_product_name_dash_title_and_body() -> None:
    payload = {
        "site": "g2",
        "product_slug": "asana",
        "product_name": "Asana",
        "title": "Onboarding is rough for new teammates",
        "body": "Took our PMs three weeks to feel productive.",
        "rating": 2,
        "helpful_count": 23,
    }
    out = extract_review_sites(payload)
    assert "Asana — Onboarding is rough" in out.text
    assert "Took our PMs" in out.text
    assert out.social_proof_weight == 23.0


def test_review_sites_helpful_count_missing_treated_as_zero() -> None:
    payload = {
        "site": "g2",
        "product_slug": "asana",
        "product_name": "Asana",
        "title": "T",
        "body": "B",
        "rating": 1,
        "helpful_count": None,
    }
    out = extract_review_sites(payload)
    assert out.social_proof_weight == 0.0


def test_extractor_against_real_reddit_fixture() -> None:
    """End-to-end: load the Reddit listing fixture, extract the first post."""
    fixture = json.loads((_FIXTURE_ROOT / "reddit" / "listing_saas.json").read_text())
    fixture.pop("_fixture_meta", None)
    children = fixture["data"]["children"]
    first = children[0]["data"]
    out = extract_reddit(first)
    assert isinstance(out, ExtractedText)
    assert len(out.text) > 0
    assert out.is_low_signal is False


def test_extractor_against_real_hn_fixture() -> None:
    fixture = json.loads((_FIXTURE_ROOT / "hn" / "search_ask_hn.json").read_text())
    fixture.pop("_fixture_meta", None)
    hits = fixture["hits"]
    out = extract_hn(hits[0])
    assert len(out.text) > 0


def test_extractor_against_real_producthunt_fixture() -> None:
    fixture = json.loads((_FIXTURE_ROOT / "producthunt" / "posts_topic.json").read_text())
    fixture.pop("_fixture_meta", None)
    edges = fixture["data"]["posts"]["edges"]
    node = edges[0]["node"]
    out = extract_producthunt(node)
    assert "Tiny Observability" in out.text
    assert out.social_proof_weight == 245.0


def test_dispatch_table_covers_every_known_source() -> None:
    assert set(EXTRACTORS.keys()) == {
        "reddit",
        "hn",
        "producthunt",
        "indiehackers",
        "review_sites",
    }
    for kind in EXTRACTORS:
        assert get_extractor(kind) is not None


def test_get_extractor_returns_none_for_unknown_source() -> None:
    assert get_extractor("yelp") is None
