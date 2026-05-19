"""Integration test for the IndieHackers ingester — real grouppage fetch.

Marked @pytest.mark.integration so `make test` skips by default; run via
`make test-all`. Requires internet access.

Per task 008 Notes: if IndieHackers actively blocks (Cloudflare challenge),
this test will fail and the operational call is to *park IH as a source*
manually (`is_active=False`) and re-prioritize task 009 (review mining)
rather than fight the block.

Per established pattern, this test does NOT write fixtures — capture lives
in `scripts/capture_indiehackers_fixture.py`.
"""

from __future__ import annotations

import socket

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from apfun.models import RawSignal, Source
from apfun.sourcing.indiehackers import ingest


def _internet_available() -> bool:
    try:
        socket.gethostbyname("www.indiehackers.com")
        return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _internet_available(), reason="no internet"),
]


def test_live_indiehackers_ingest_inserts_rows(session: Session) -> None:
    src = Source(
        kind="indiehackers",
        name="ih:main-live-smoke",
        config_json={"groups": ["main"], "since_hours": 24},
    )
    session.add(src)
    session.flush()

    result = ingest(session, src)
    session.commit()

    if result.error_class == "parse_error" and not result.items_captured:
        pytest.fail(
            "IH returned a page that didn't yield posts via either __NEXT_DATA__ "
            "or HTML scrape. If Cloudflare is challenging us (look for status 403 "
            "in result.status_codes), per task 008 Notes: park this source "
            "(is_active=False) and re-prioritize task 009."
        )

    assert result.items_captured >= 1, (
        f"expected ≥1 row from real IH grouppage, got {result.items_captured} "
        f"(status_codes={result.status_codes}, error_class={result.error_class})"
    )
    rows = session.execute(select(RawSignal).where(RawSignal.source_id == src.id)).scalars().all()
    assert len(rows) >= 1
    for row in rows[:5]:
        assert row.external_id  # slug
        assert isinstance(row.payload_json, dict)
        assert "slug" in row.payload_json
