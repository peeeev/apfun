"""Unit tests for the shared source-identifier helper (task 014-fix-1)."""

from __future__ import annotations

import pytest

from apfun.pipeline._source_identifier import source_identifier


@pytest.mark.parametrize(
    ("kind", "payload", "expected"),
    [
        ("reddit", {"subreddit": "SaaS"}, "r/SaaS"),
        ("hn", {"_apfun_query": "wishes"}, "hn:wishes"),
        ("producthunt", {"_apfun_surface": "topics"}, "ph:topics"),
        ("indiehackers", {"_apfun_group": "starting-up"}, "ih:starting-up"),
        ("review_sites", {"site": "g2", "product_slug": "asana"}, "g2:asana"),
    ],
)
def test_source_identifier_per_kind(kind: str, payload: dict, expected: str) -> None:
    assert source_identifier(kind, payload) == expected


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("reddit", {}),
        ("hn", {}),
        ("producthunt", {}),
        ("indiehackers", {}),
        ("review_sites", {}),
    ],
)
def test_source_identifier_falls_back_when_key_missing(kind: str, payload: dict) -> None:
    # No expected key in payload → a non-empty, sensible fallback (never crashes).
    result = source_identifier(kind, payload)
    assert result
    assert ":" not in result or result.startswith(("hn", "ph", "ih"))


def test_source_identifier_none_payload() -> None:
    assert source_identifier("reddit", None) == "reddit"


def test_source_identifier_unknown_kind() -> None:
    assert source_identifier("mastodon", {"foo": "bar"}) == "mastodon"


def test_review_sites_site_only_fallback() -> None:
    # site present but slug missing → just the site name, no trailing colon.
    assert source_identifier("review_sites", {"site": "capterra"}) == "capterra"
