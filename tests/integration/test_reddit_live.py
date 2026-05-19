"""Integration test for the Reddit ingester — hits a real subreddit.

Marked @pytest.mark.integration so `make test` skips by default; run via
`make test-all`. Requires internet access and a valid APFUN_REDDIT_USERNAME
(the conftest sets a sentinel handle but production-realistic usernames work
better against Reddit's UA-block heuristics).

Per orchestrator feedback 011 Q3: this test does NOT write fixtures. Fixture
capture lives in `scripts/capture_reddit_fixture.py` as a separate, intentional
action. The integration test only asserts: at least one row inserted, and the
schema contract holds against the live response.
"""

from __future__ import annotations

import socket

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from apfun.models import RawSignal, Source
from apfun.sourcing.reddit import ingest


def _internet_available() -> bool:
    try:
        socket.gethostbyname("www.reddit.com")
        return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _internet_available(), reason="no internet"),
]


def test_live_reddit_ingest_inserts_rows(session: Session) -> None:
    src = Source(
        kind="reddit",
        name="r/programming",
        config_json={"subreddits": ["programming"], "fetch_kind": "new"},
    )
    session.add(src)
    session.flush()

    result = ingest(session, src)
    session.commit()

    assert result.items_captured >= 1, (
        f"expected ≥1 row inserted from a real subreddit, got {result.items_captured} "
        f"(status_codes={result.status_codes}, error_class={result.error_class})"
    )
    rows = session.execute(select(RawSignal).where(RawSignal.source_id == src.id)).scalars().all()
    assert len(rows) >= 1

    # Spot-check: each row has the fields the contract test asserts on.
    for row in rows[:5]:
        assert row.external_id and row.external_id.startswith("t3_")
        assert isinstance(row.payload_json, dict)
        assert "subreddit" in row.payload_json
        assert "title" in row.payload_json
