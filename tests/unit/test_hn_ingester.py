"""Unit tests for `apfun.sourcing.hn.ingest`.

Mocks `httpx.Client` against the synthetic fixture in `tests/fixtures/hn/`.
Verifies: dedup, points-threshold filtering, payload tagging with the query
that surfaced the hit, rate-limit `acquire()` invocation, terminal/transient
status handling.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from apfun.models import RawSignal, Source
from apfun.sourcing import _base as base_module
from apfun.sourcing import hn as hn_module
from apfun.sourcing.hn import ingest

_FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "hn" / "search_ask_hn.json"


def _fixture_body() -> dict[str, Any]:
    data = json.loads(_FIXTURE_PATH.read_text())
    data.pop("_fixture_meta", None)
    return data


def _make_mock_client(status: int = 200, body: dict[str, Any] | None = None) -> MagicMock:
    if body is None:
        body = _fixture_body()
    response = MagicMock(spec=httpx.Response)
    response.status_code = status
    response.json.return_value = body
    response.raise_for_status = MagicMock()
    client = MagicMock(spec=httpx.Client)
    client.get.return_value = response
    return client


@pytest.fixture
def hn_source(session: Session) -> Source:
    src = Source(
        kind="hn",
        name="hn:ask-hn",
        config_json={
            "queries": ["tool you wish existed"],
            "since_hours": 24,
            "min_story_points": 3,
            "min_comment_points": 1,
        },
    )
    session.add(src)
    session.flush()
    return src


def test_ingest_inserts_hits_above_threshold(session: Session, hn_source: Source) -> None:
    client = _make_mock_client()
    result = ingest(session, hn_source, client=client)
    session.commit()

    # Fixture has 4 hits: 64-pt story, 12-pt story, 4-pt comment, 1-pt story.
    # min_story_points=3, min_comment_points=1 → 1-pt story filtered, rest kept = 3.
    assert result.items_captured == 3
    assert result.status_codes == [200]
    rows = session.execute(select(RawSignal).order_by(RawSignal.id)).scalars().all()
    assert len(rows) == 3
    external_ids = {r.external_id for r in rows}
    assert external_ids == {"44000001", "44000002", "44000003"}
    assert "44000004" not in external_ids, "1-point story should be filtered"


def test_payload_carries_apfun_query_tag(session: Session, hn_source: Source) -> None:
    client = _make_mock_client()
    ingest(session, hn_source, client=client)
    session.commit()

    rows = session.execute(select(RawSignal).order_by(RawSignal.id)).scalars().all()
    for row in rows:
        assert isinstance(row.payload_json, dict)
        assert row.payload_json.get("_apfun_query") == "tool you wish existed", (
            "every row should record which configured query surfaced it"
        )


def test_dedup_on_second_run(session: Session, hn_source: Source) -> None:
    client = _make_mock_client()
    first = ingest(session, hn_source, client=client)
    session.commit()
    assert first.items_captured == 3

    second = ingest(session, hn_source, client=client)
    session.commit()
    assert second.items_captured == 0
    rows = session.execute(select(RawSignal)).scalars().all()
    assert len(rows) == 3


def test_custom_thresholds_override_defaults(session: Session) -> None:
    src = Source(
        kind="hn",
        name="hn:lower-bar",
        config_json={
            "queries": ["tool you wish existed"],
            "min_story_points": 1,  # admit the 1-point story too
            "min_comment_points": 1,
        },
    )
    session.add(src)
    session.flush()

    client = _make_mock_client()
    result = ingest(session, src, client=client)
    session.commit()
    assert result.items_captured == 4, "lowering min_story_points should admit the 1-pt story"


def test_rate_limiter_acquired_per_query(
    session: Session, hn_source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    acquire_count = {"n": 0}

    def fake_acquire() -> None:
        acquire_count["n"] += 1

    monkeypatch.setattr(hn_module._BUCKET, "acquire", fake_acquire)
    client = _make_mock_client()
    ingest(session, hn_source, client=client)

    assert acquire_count["n"] == 1, "one query → one acquire"


def test_user_agent_header_present(session: Session, hn_source: Source) -> None:
    client = _make_mock_client()
    ingest(session, hn_source, client=client)
    _, kwargs = client.get.call_args
    headers = kwargs["headers"]
    assert headers["User-Agent"].startswith("apfun-funnel/")


def test_terminal_status_returns_without_retry(
    session: Session, hn_source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(base_module.time, "sleep", lambda _s: None)
    client = _make_mock_client(status=400, body={})
    result = ingest(session, hn_source, client=client)
    assert result.status_codes == [400]
    assert result.items_captured == 0
    assert client.get.call_count == 1  # no retry on terminal


def test_transient_5xx_retries_then_gives_up(
    session: Session, hn_source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(base_module.time, "sleep", lambda _s: None)
    client = _make_mock_client(status=503, body={})
    result = ingest(session, hn_source, client=client)
    assert result.status_codes == [503]
    assert client.get.call_count == base_module.MAX_RETRIES


def test_numeric_filter_passed_for_since_hours(session: Session, hn_source: Source) -> None:
    client = _make_mock_client()
    ingest(session, hn_source, client=client)
    _, kwargs = client.get.call_args
    params = kwargs["params"]
    assert "numericFilters" in params
    assert params["numericFilters"].startswith("created_at_i>")


def test_overlapping_queries_persist_unique_rows(session: Session) -> None:
    """Regression: ingester runs multiple queries (per-source `queries` list);
    HN often returns overlapping `objectID`s across related queries. Prior
    to the SAVEPOINT fix, the second query's collision rolled back the
    first query's successful inserts, leaving 0 rows in the DB despite
    `items_captured` reporting non-zero.

    Surfaced by runbook 001 on 2026-05-22.
    """
    src = Source(
        kind="hn",
        name="hn:overlap",
        config_json={
            # Three queries that all return the same fixture body — the
            # second and third query's hits will be content_hash duplicates.
            "queries": ["q1", "q2", "q3"],
            "since_hours": 24,
            "min_story_points": 1,
            "min_comment_points": 0,
        },
    )
    session.add(src)
    session.flush()

    client = _make_mock_client()
    result = ingest(session, src, client=client)
    session.commit()

    # Fixture has 4 hits; min_story_points=1 admits all of them.
    # First query inserts 4. Queries 2 + 3 hit collisions on every hit.
    assert result.items_captured == 4

    # Critical: a fresh session sees the 4 rows, not 0.
    from sqlalchemy.orm import Session as _S

    with _S(session.bind) as fresh:
        n = fresh.execute(select(RawSignal)).scalars().all()
        assert len(n) == 4, (
            f"fresh-session must see all 4 rows after overlapping queries; got "
            f"{len(n)}. If 0, the SAVEPOINT regression is back."
        )


def test_content_hash_uses_object_id() -> None:
    """objectID alone is the content_hash input — Algolia gives it as the canonical key."""
    h_a = hn_module._content_hash("44000001")
    h_b = hn_module._content_hash("44000002")
    assert h_a != h_b
    assert h_a == hn_module._content_hash("44000001")
