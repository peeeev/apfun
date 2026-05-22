"""Integration test for the Reddit ingester — hits a real subreddit via OAuth.

Marked @pytest.mark.integration so `make test` skips by default; run via
`make test-all`. Requires internet access plus real OAuth credentials:
`APFUN_REDDIT_USERNAME`, `APFUN_REDDIT_CLIENT_ID`, `APFUN_REDDIT_CLIENT_SECRET`
(register a "script" app at https://www.reddit.com/prefs/apps — task 005b
migrated this ingester to OAuth after datacenter-IP blocking persisted on the
anonymous path).

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

from apfun.config import settings
from apfun.models import RawSignal, Source
from apfun.sourcing.reddit import ingest


def _internet_available() -> bool:
    try:
        socket.gethostbyname("oauth.reddit.com")
        return True
    except OSError:
        return False


def _real_oauth_creds_available() -> bool:
    """Sentinels from conftest don't count — the real token endpoint rejects them."""
    return settings.reddit_client_id not in (
        "",
        "test_client_id",
    ) and settings.reddit_client_secret not in ("", "test_client_secret")


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _internet_available(), reason="no internet"),
    pytest.mark.skipif(
        not _real_oauth_creds_available(),
        reason="APFUN_REDDIT_CLIENT_ID/SECRET not set (conftest sentinels won't authenticate)",
    ),
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
