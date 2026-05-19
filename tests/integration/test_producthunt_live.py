"""Integration test for the ProductHunt ingester — real GraphQL API call.

Marked @pytest.mark.integration so `make test` skips by default; run via
`make test-all`. Requires `APFUN_PRODUCTHUNT_TOKEN` (Client-only token,
per task 007 spec / feedback 013 Q2) and internet access.

Per established pattern, this test does NOT write fixtures. Capture lives
in `scripts/capture_producthunt_fixture.py`.
"""

from __future__ import annotations

import os
import socket

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from apfun.models import RawSignal, Source
from apfun.sourcing.producthunt import ingest


def _internet_available() -> bool:
    try:
        socket.gethostbyname("api.producthunt.com")
        return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("APFUN_PRODUCTHUNT_TOKEN"), reason="APFUN_PRODUCTHUNT_TOKEN not set"
    ),
    pytest.mark.skipif(not _internet_available(), reason="no internet"),
]


def test_live_producthunt_ingest_inserts_rows(session: Session) -> None:
    src = Source(
        kind="producthunt",
        name="ph:dev-tools-live-smoke",
        config_json={
            "surface": "topic",
            "topics": ["developer-tools"],
            "n_days": 7,
            "min_votes_count": 1,
        },
    )
    session.add(src)
    session.flush()

    result = ingest(session, src)
    session.commit()

    assert result.items_captured >= 1, (
        f"expected ≥1 row from real ProductHunt query, got {result.items_captured} "
        f"(status_codes={result.status_codes}, error_class={result.error_class})"
    )
    rows = session.execute(select(RawSignal).where(RawSignal.source_id == src.id)).scalars().all()
    assert len(rows) >= 1
    for row in rows[:5]:
        assert row.external_id  # slug
        assert isinstance(row.payload_json, dict)
        assert "slug" in row.payload_json
        assert row.payload_json.get("_apfun_surface") == "topic"
