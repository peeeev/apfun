"""Integration test for the Reddit ingester — hits a real subreddit via proxy.

Marked @pytest.mark.integration so `make test` skips by default; run via
`make test-all`. Requires internet access plus a configured residential proxy
(`APFUN_REDDIT_HTTP_PROXY`). Task 005c migrated this ingester to a residential
proxy + browser-mimicking UA pool after Reddit's datacenter-IP block and
June-2025 web-frontend UA filtering made both the anonymous and OAuth paths
unviable from this server.

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
        socket.gethostbyname("www.reddit.com")
        return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _internet_available(), reason="no internet"),
    pytest.mark.skipif(
        not settings.reddit_http_proxy,
        reason="APFUN_REDDIT_HTTP_PROXY not set (Reddit blocks datacenter IPs)",
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

    # No client passed → ingest() builds the proxy-routed client itself.
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
