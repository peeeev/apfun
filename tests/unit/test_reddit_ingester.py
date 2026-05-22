"""Unit tests for `apfun.sourcing.reddit.ingest`.

Mocks `httpx.Client` against the synthetic fixture in `tests/fixtures/reddit/`.
Verifies: dedup on re-ingest, deletion tagging, UA-header + OAuth bearer
presence, content-hash stability, rate-limit `acquire()` invocation, listing
URL uses the oauth.reddit.com base.

OAuth (task 005b) note: every test uses the `stub_reddit_auth` fixture so
the production token endpoint is never hit. Tests for the auth lifecycle
itself live in `test_reddit_oauth.py`.
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
from apfun.sourcing import reddit as reddit_module
from apfun.sourcing.reddit import ingest

_FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "reddit" / "listing_saas.json"


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


@pytest.fixture(autouse=True)
def stub_reddit_auth(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace the OAuth singleton with a stub that returns a fixed token.

    Reset the module-level singleton so prior tests don't leak. The stub's
    `get_token` always returns the same string, which is what the tests
    assert against in the Bearer header check.
    """
    monkeypatch.setattr(reddit_module, "_auth", None)
    stub = MagicMock()
    stub.get_token.return_value = "test-bearer"
    stub.invalidate = MagicMock()
    monkeypatch.setattr(reddit_module, "_get_auth", lambda: stub)
    return stub


@pytest.fixture
def reddit_source(session: Session) -> Source:
    src = Source(
        kind="reddit",
        name="r/SaaS",
        config_json={"subreddits": ["SaaS"], "fetch_kind": "new"},
    )
    session.add(src)
    session.flush()
    return src


def test_ingest_inserts_signals_with_correct_metadata(
    session: Session, reddit_source: Source
) -> None:
    client = _make_mock_client()
    result = ingest(session, reddit_source, client=client)
    session.commit()

    assert result.items_captured == 4
    assert result.status_codes == [200]
    rows = session.execute(select(RawSignal).order_by(RawSignal.id)).scalars().all()
    assert len(rows) == 4

    first = rows[0]
    assert first.source_id == reddit_source.id
    assert first.external_id == "t3_abc1"
    assert first.url == "https://www.reddit.com/r/SaaS/comments/abc1/what_billing_tool/"
    assert isinstance(first.payload_json, dict)
    assert first.payload_json["subreddit"] == "SaaS"
    assert first.payload_json["title"].startswith("What billing tool")


def test_ingest_dedupes_on_second_run(session: Session, reddit_source: Source) -> None:
    client = _make_mock_client()
    first = ingest(session, reddit_source, client=client)
    session.commit()
    assert first.items_captured == 4

    second = ingest(session, reddit_source, client=client)
    session.commit()
    assert second.items_captured == 0
    rows = session.execute(select(RawSignal)).scalars().all()
    assert len(rows) == 4, "second pass should not double-insert"


def test_deletion_tagging(session: Session, reddit_source: Source) -> None:
    client = _make_mock_client()
    ingest(session, reddit_source, client=client)
    session.commit()

    rows = session.execute(select(RawSignal).order_by(RawSignal.id)).scalars().all()
    by_ext = {r.external_id: r for r in rows}

    # Synthetic fixture: abc2 has [deleted] selftext, abc3 has [removed] selftext.
    deleted = by_ext["t3_abc2"]
    assert isinstance(deleted.payload_json, dict)
    assert deleted.payload_json.get("is_deleted") is True
    assert deleted.payload_json.get("deletion_marker") == "[deleted]"

    removed = by_ext["t3_abc3"]
    assert isinstance(removed.payload_json, dict)
    assert removed.payload_json.get("is_deleted") is True
    assert removed.payload_json.get("deletion_marker") == "[removed]"

    # And a non-deleted one shouldn't be tagged.
    alive = by_ext["t3_abc1"]
    assert isinstance(alive.payload_json, dict)
    assert "is_deleted" not in alive.payload_json
    assert "deletion_marker" not in alive.payload_json


def test_user_agent_header_format(session: Session, reddit_source: Source) -> None:
    client = _make_mock_client()
    ingest(session, reddit_source, client=client)

    assert client.get.call_count == 1
    args, kwargs = client.get.call_args
    headers = kwargs["headers"]
    ua = headers["User-Agent"]
    assert ua.startswith("apfun-funnel:v0.1 (by /u/")
    assert ua.endswith(")")
    assert "apfun_test_runner" in ua, "should pick up reddit_username from settings"
    # OAuth: every listing fetch carries a Bearer header from the auth stub.
    assert headers["Authorization"] == "Bearer test-bearer"


def test_listing_uses_oauth_base_url(session: Session, reddit_source: Source) -> None:
    """Listing fetches hit `oauth.reddit.com`, not `www.reddit.com`. Task 005b."""
    client = _make_mock_client()
    ingest(session, reddit_source, client=client)
    args, _ = client.get.call_args
    url = args[0]
    assert url.startswith("https://oauth.reddit.com/r/SaaS/")
    assert url.endswith("/new.json")


def test_rate_limiter_acquired_per_call(
    session: Session, reddit_source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    acquire_count = {"n": 0}

    def fake_acquire() -> None:
        acquire_count["n"] += 1

    monkeypatch.setattr(reddit_module._BUCKET, "acquire", fake_acquire)
    client = _make_mock_client()
    ingest(session, reddit_source, client=client)

    assert acquire_count["n"] == 1, "one listing call → one acquire"


def test_terminal_status_returns_without_retry(
    session: Session, reddit_source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Speed up: no real sleeps even if a retry path is taken. Retry sleep
    # lives in apfun.sourcing._base after the refactor.
    monkeypatch.setattr(base_module.time, "sleep", lambda _s: None)
    client = _make_mock_client(status=404, body={})
    result = ingest(session, reddit_source, client=client)

    assert result.status_codes == [404]
    assert result.items_captured == 0
    # 404 is terminal — should NOT retry.
    assert client.get.call_count == 1


def test_transient_5xx_retries_then_gives_up(
    session: Session, reddit_source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(base_module.time, "sleep", lambda _s: None)
    client = _make_mock_client(status=503, body={})
    result = ingest(session, reddit_source, client=client)

    assert result.status_codes == [503]
    assert client.get.call_count == base_module.MAX_RETRIES
    # 503 is not terminal → counter stays untouched (batch layer's job anyway).


def test_content_hash_uses_subreddit_external_id_title_body() -> None:
    """Two posts in different subreddits with identical title+body get distinct hashes."""
    h_a = reddit_module._content_hash("SaaS", "abc1", "title", "body")
    h_b = reddit_module._content_hash("Entrepreneur", "abc1", "title", "body")
    assert h_a != h_b
    # And identical inputs are stable.
    assert h_a == reddit_module._content_hash("SaaS", "abc1", "title", "body")
