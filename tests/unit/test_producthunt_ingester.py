"""Unit tests for `apfun.sourcing.producthunt.ingest`.

Mocks `httpx.Client` against the synthetic fixture in `tests/fixtures/producthunt/`.
Verifies: dedup, vote-count filtering, surface tagging, retry behavior, the
missing-token clean-no-op path (which is the *defining* behavior of this
ingester vs the others — see task 007 spec).
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

from apfun.config import settings
from apfun.models import RawSignal, Source
from apfun.sourcing import _base as base_module
from apfun.sourcing import producthunt as ph_module
from apfun.sourcing.producthunt import ingest

_FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "producthunt" / "posts_topic.json"


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
    client.post.return_value = response
    return client


@pytest.fixture
def ph_token(monkeypatch: pytest.MonkeyPatch) -> str:
    """Set a sentinel token so the happy path runs; tests that exercise the
    missing-token path use `ph_no_token` instead."""
    token = "test_token_sentinel"
    monkeypatch.setattr(settings, "producthunt_token", token)
    return token


@pytest.fixture
def ph_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "producthunt_token", "")


@pytest.fixture
def ph_source(session: Session) -> Source:
    src = Source(
        kind="producthunt",
        name="ph:dev-tools-topic",
        config_json={
            "surface": "topic",
            "topics": ["developer-tools"],
            "n_days": 1,
            "min_votes_count": 10,
        },
    )
    session.add(src)
    session.flush()
    return src


def test_ingest_inserts_posts_above_vote_threshold(
    session: Session, ph_source: Source, ph_token: str
) -> None:
    client = _make_mock_client()
    result = ingest(session, ph_source, client=client)
    session.commit()

    # Fixture has 4 posts with votes [245, 88, 17, 3].
    # min_votes_count=10 → keep first three, drop the 3-vote one. → 3 rows.
    assert result.items_captured == 3
    assert result.status_codes == [200]
    rows = session.execute(select(RawSignal).order_by(RawSignal.id)).scalars().all()
    assert len(rows) == 3
    slugs = {r.external_id for r in rows}
    assert slugs == {
        "tiny-observability-for-saas",
        "stripe-billing-helper",
        "self-hosted-linear-alt",
    }
    assert "low-signal-launch" not in slugs


def test_payload_carries_apfun_surface_tag(
    session: Session, ph_source: Source, ph_token: str
) -> None:
    client = _make_mock_client()
    ingest(session, ph_source, client=client)
    session.commit()

    rows = session.execute(select(RawSignal).order_by(RawSignal.id)).scalars().all()
    for row in rows:
        assert isinstance(row.payload_json, dict)
        assert row.payload_json.get("_apfun_surface") == "topic"


def test_dedup_on_second_run(session: Session, ph_source: Source, ph_token: str) -> None:
    client = _make_mock_client()
    first = ingest(session, ph_source, client=client)
    session.commit()
    assert first.items_captured == 3

    second = ingest(session, ph_source, client=client)
    session.commit()
    assert second.items_captured == 0
    rows = session.execute(select(RawSignal)).scalars().all()
    assert len(rows) == 3


def test_vote_threshold_can_be_raised_to_filter_more(session: Session, ph_token: str) -> None:
    src = Source(
        kind="producthunt",
        name="ph:high-bar",
        config_json={
            "surface": "topic",
            "topics": ["developer-tools"],
            "min_votes_count": 100,
        },
    )
    session.add(src)
    session.flush()
    client = _make_mock_client()
    result = ingest(session, src, client=client)
    session.commit()
    # Only the 245-vote post survives min_votes_count=100.
    assert result.items_captured == 1


def test_missing_token_is_clean_noop(
    session: Session, ph_source: Source, ph_no_token: None
) -> None:
    """The defining behavior: missing token → log WARNING, return empty IngestResult."""
    client = _make_mock_client()
    result = ingest(session, ph_source, client=client)
    session.commit()

    assert result.items_captured == 0
    assert result.error_class == "missing_token"
    assert result.status_codes == []
    # No HTTP call should have happened.
    assert client.post.call_count == 0
    # No raw_signals should have been written.
    rows = session.execute(select(RawSignal)).scalars().all()
    assert len(rows) == 0


def test_bearer_token_header_sent(session: Session, ph_source: Source, ph_token: str) -> None:
    client = _make_mock_client()
    ingest(session, ph_source, client=client)
    _, kwargs = client.post.call_args
    assert kwargs["headers"]["Authorization"] == f"Bearer {ph_token}"


def test_rate_limiter_acquired_per_query(
    session: Session,
    ph_source: Source,
    ph_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acquire_count = {"n": 0}

    def fake_acquire() -> None:
        acquire_count["n"] += 1

    monkeypatch.setattr(ph_module._BUCKET, "acquire", fake_acquire)
    client = _make_mock_client()
    ingest(session, ph_source, client=client)

    assert acquire_count["n"] == 1, "one topic → one acquire"


def test_topic_fanout_runs_multiple_queries(
    session: Session, ph_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = Source(
        kind="producthunt",
        name="ph:multi-topic",
        config_json={
            "surface": "topic",
            "topics": ["developer-tools", "productivity"],
            "min_votes_count": 10,
        },
    )
    session.add(src)
    session.flush()
    monkeypatch.setattr(ph_module._BUCKET, "acquire", lambda: None)
    client = _make_mock_client()
    result = ingest(session, src, client=client)

    assert client.post.call_count == 2, "two topics → two GraphQL calls"
    # Both queries return the same fixture; dedup means only 3 rows inserted.
    assert result.items_captured == 3
    assert result.status_codes == [200, 200]


def test_leaderboard_surface_runs_one_query_no_topic(
    session: Session, ph_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = Source(
        kind="producthunt",
        name="ph:daily-leaderboard",
        config_json={
            "surface": "leaderboard",
            "leaderboard": "daily",
            "min_votes_count": 5,
        },
    )
    session.add(src)
    session.flush()
    monkeypatch.setattr(ph_module._BUCKET, "acquire", lambda: None)
    client = _make_mock_client()
    result = ingest(session, src, client=client)

    assert client.post.call_count == 1, "leaderboard surface → one query"
    # min_votes_count=5 keeps three of four fixture posts (drops the 3-vote one).
    assert result.items_captured == 3


def test_terminal_status_returns_without_retry(
    session: Session,
    ph_source: Source,
    ph_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(base_module.time, "sleep", lambda _s: None)
    client = _make_mock_client(status=401, body={})
    result = ingest(session, ph_source, client=client)
    assert result.status_codes == [401]
    assert result.items_captured == 0
    assert client.post.call_count == 1


def test_transient_5xx_retries_then_gives_up(
    session: Session,
    ph_source: Source,
    ph_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(base_module.time, "sleep", lambda _s: None)
    client = _make_mock_client(status=503, body={})
    result = ingest(session, ph_source, client=client)
    assert result.status_codes == [503]
    assert client.post.call_count == base_module.MAX_RETRIES


def test_content_hash_uses_slug() -> None:
    h_a = ph_module._content_hash("tiny-observability-for-saas")
    h_b = ph_module._content_hash("stripe-billing-helper")
    assert h_a != h_b
    assert h_a == ph_module._content_hash("tiny-observability-for-saas")
