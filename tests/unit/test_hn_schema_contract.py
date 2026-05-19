"""Contract test for the HN Algolia search-API response shape we parse.

Asserts that every field the ingester depends on is present in a captured
fixture. If this test fails after a fixture refresh, Algolia changed their
response shape — investigate before adjusting the parser. See CLAUDE.md →
Project conventions → "Contract tests for external schemas."
"""

from __future__ import annotations

import json
from pathlib import Path

_FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "hn" / "search_ask_hn.json"


def _load() -> dict[str, object]:
    data = json.loads(_FIXTURE_PATH.read_text())
    data.pop("_fixture_meta", None)
    return data


def test_response_envelope_shape() -> None:
    data = _load()
    assert isinstance(data.get("hits"), list)
    assert isinstance(data.get("nbHits"), int)
    assert isinstance(data.get("hitsPerPage"), int)
    assert "query" in data


def test_hits_carry_required_fields() -> None:
    data = _load()
    hits = data["hits"]
    assert isinstance(hits, list) and len(hits) > 0, "fixture has no hits"
    required = {
        "objectID",
        "author",
        "points",
        "created_at",
        "created_at_i",
        "_tags",
    }
    missing_per_hit: list[tuple[int, set[str]]] = []
    for idx, hit in enumerate(hits):
        assert isinstance(hit, dict), f"hit {idx} not a dict"
        missing = required - set(hit.keys())
        if missing:
            missing_per_hit.append((idx, missing))
    assert not missing_per_hit, (
        f"HN fixture is missing fields the ingester reads: {missing_per_hit}. "
        "Re-capture via scripts/capture_hn_fixture.py and update assertions if "
        "Algolia actually changed the shape."
    )


def test_hit_field_types() -> None:
    data = _load()
    for idx, hit in enumerate(data["hits"]):  # type: ignore[union-attr]
        assert isinstance(hit["objectID"], (str, int)), f"hit {idx} objectID type"
        assert isinstance(hit["points"], int), f"hit {idx} points type"
        assert isinstance(hit["created_at"], str), f"hit {idx} created_at type"
        assert isinstance(hit["created_at_i"], int), f"hit {idx} created_at_i type"
        assert isinstance(hit["_tags"], list), f"hit {idx} _tags type"


def test_at_least_one_story_and_one_comment_in_fixture() -> None:
    """Sanity check that the synthetic fixture covers both hit types we ingest."""
    data = _load()
    has_story = False
    has_comment = False
    for hit in data["hits"]:  # type: ignore[union-attr]
        tags = hit.get("_tags") or []
        if "story" in tags:
            has_story = True
        if "comment" in tags:
            has_comment = True
    assert has_story, "fixture should contain at least one story-tagged hit"
    assert has_comment, "fixture should contain at least one comment-tagged hit"
