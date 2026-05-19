"""Contract test for the Reddit listing JSON shape we parse.

Assert that every field the ingester depends on is present in a captured
fixture. If this test fails after a fixture refresh, Reddit changed their
response shape — investigate before adjusting the parser. See CLAUDE.md →
Project conventions → "Contract tests for external schemas."
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "reddit" / "listing_saas.json"


def _load() -> dict[str, object]:
    data = json.loads(_FIXTURE_PATH.read_text())
    data.pop("_fixture_meta", None)
    return data


def test_listing_envelope_shape() -> None:
    data = _load()
    assert data.get("kind") == "Listing"
    assert isinstance(data.get("data"), dict)
    inner = data["data"]
    assert isinstance(inner, dict)
    assert isinstance(inner.get("children"), list)


def test_child_post_required_fields() -> None:
    data = _load()
    children = data["data"]["children"]  # type: ignore[index]
    assert isinstance(children, list) and len(children) > 0, "fixture has no posts"
    required = {
        "id",
        "subreddit",
        "title",
        "selftext",
        "score",
        "num_comments",
        "created_utc",
        "permalink",
        "url",
    }
    missing_per_child: list[tuple[int, set[str]]] = []
    for idx, child in enumerate(children):
        assert isinstance(child, dict), f"child {idx} not a dict"
        post = child.get("data")
        assert isinstance(post, dict), f"child {idx} missing .data dict"
        missing = required - set(post.keys())
        if missing:
            missing_per_child.append((idx, missing))
    assert not missing_per_child, (
        f"Reddit fixture is missing fields the ingester reads: {missing_per_child}. "
        "Re-capture via scripts/capture_reddit_fixture.py and update assertions if "
        "Reddit actually changed the shape."
    )


@pytest.mark.parametrize("idx", [0, 1, 2, 3])
def test_field_types(idx: int) -> None:
    """Spot-check that numeric fields really are numeric across the fixture."""
    data = _load()
    children = data["data"]["children"]  # type: ignore[index]
    if idx >= len(children):
        pytest.skip(f"fixture has only {len(children)} children")
    post = children[idx]["data"]
    assert isinstance(post["score"], int)
    assert isinstance(post["num_comments"], int)
    assert isinstance(post["created_utc"], (int, float))
    assert isinstance(post["title"], str)
    assert isinstance(post["selftext"], str)
