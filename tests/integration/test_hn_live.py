"""Integration test for the HN Algolia ingester — hits the real API.

Marked @pytest.mark.integration so `make test` skips by default; run via
`make test-all`. Algolia HN search is unauthenticated and generous, so this
test is safe to run without secrets — but does require internet access.

Per the established pattern, this test does NOT write fixtures. Capture
lives in `scripts/capture_hn_fixture.py`.
"""

from __future__ import annotations

import socket

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from apfun.models import RawSignal, Source
from apfun.sourcing.hn import ingest


def _internet_available() -> bool:
    try:
        socket.gethostbyname("hn.algolia.com")
        return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _internet_available(), reason="no internet"),
]


def test_live_hn_ingest_inserts_rows(session: Session) -> None:
    src = Source(
        kind="hn",
        name="hn:ask-hn-live-smoke",
        config_json={
            "queries": ["Ask HN"],
            "since_hours": 168,  # one week — generous window for a smoke test
            "min_story_points": 1,
            "min_comment_points": 0,
        },
    )
    session.add(src)
    session.flush()

    result = ingest(session, src)
    session.commit()

    assert result.items_captured >= 1, (
        f"expected ≥1 row from a real Algolia query, got {result.items_captured} "
        f"(status_codes={result.status_codes}, error_class={result.error_class})"
    )
    rows = session.execute(select(RawSignal).where(RawSignal.source_id == src.id)).scalars().all()
    assert len(rows) >= 1
    for row in rows[:5]:
        assert row.external_id
        assert isinstance(row.payload_json, dict)
        assert "objectID" in row.payload_json
