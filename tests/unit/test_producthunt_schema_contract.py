"""Contract test for the ProductHunt GraphQL response shape we parse.

Asserts that every field the ingester depends on is present in a captured
fixture. If this test fails after a fixture refresh, ProductHunt's GraphQL
schema changed — investigate before adjusting the parser. See CLAUDE.md →
Project conventions → "Contract tests for external schemas."
"""

from __future__ import annotations

import json
from pathlib import Path

_FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "producthunt" / "posts_topic.json"


def _load() -> dict[str, object]:
    data = json.loads(_FIXTURE_PATH.read_text())
    data.pop("_fixture_meta", None)
    return data


def test_response_envelope_shape() -> None:
    data = _load()
    inner = data.get("data")
    assert isinstance(inner, dict)
    posts = inner.get("posts")
    assert isinstance(posts, dict)
    assert isinstance(posts.get("edges"), list)
    page_info = posts.get("pageInfo")
    assert isinstance(page_info, dict)
    assert "endCursor" in page_info
    assert "hasNextPage" in page_info


def test_post_node_required_fields() -> None:
    data = _load()
    edges = data["data"]["posts"]["edges"]  # type: ignore[index]
    assert isinstance(edges, list) and len(edges) > 0
    required = {
        "id",
        "slug",
        "name",
        "tagline",
        "description",
        "url",
        "votesCount",
        "commentsCount",
        "featuredAt",
        "topics",
        "makers",
    }
    missing_per_edge: list[tuple[int, set[str]]] = []
    for idx, edge in enumerate(edges):
        assert isinstance(edge, dict), f"edge {idx} not a dict"
        node = edge.get("node")
        assert isinstance(node, dict), f"edge {idx} missing .node"
        missing = required - set(node.keys())
        if missing:
            missing_per_edge.append((idx, missing))
    assert not missing_per_edge, (
        f"ProductHunt fixture missing fields the ingester reads: {missing_per_edge}. "
        "Re-capture via scripts/capture_producthunt_fixture.py and update assertions "
        "if PH actually changed the schema."
    )


def test_topics_and_makers_subshape() -> None:
    data = _load()
    edges = data["data"]["posts"]["edges"]  # type: ignore[index]
    for idx, edge in enumerate(edges):
        node = edge["node"]
        topics = node.get("topics")
        assert isinstance(topics, dict), f"edge {idx} topics not a dict"
        topic_edges = topics.get("edges")
        assert isinstance(topic_edges, list), f"edge {idx} topics.edges not a list"
        for t in topic_edges:
            assert isinstance(t, dict)
            assert isinstance(t.get("node"), dict)
            assert "name" in t["node"] and "slug" in t["node"]

        makers = node.get("makers")
        assert isinstance(makers, dict)
        maker_edges = makers.get("edges")
        assert isinstance(maker_edges, list)
        for m in maker_edges:
            assert isinstance(m, dict)
            assert isinstance(m.get("node"), dict)
            assert "username" in m["node"]


def test_votes_count_is_int() -> None:
    data = _load()
    for edge in data["data"]["posts"]["edges"]:  # type: ignore[index]
        node = edge["node"]
        assert isinstance(node["votesCount"], int)
        assert isinstance(node["commentsCount"], int)
