"""Integration test for the review-site miner — real Playwright fetch.

Marked @pytest.mark.integration; run via `make test-all`. Hits one product per
site for one page each. If anti-bot blocks (Cloudflare challenge,
fingerprinting), per task 009 + feedback 014 the answer is the CSV importer,
not stealth Playwright plugins. The test surfaces that path explicitly in its
failure message.

Skipped automatically when:
- No internet access
- Playwright not installable (e.g. running outside the dev container with no
  chromium binary)
"""

from __future__ import annotations

import socket

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from apfun.models import RawSignal, Source
from apfun.sourcing.review_sites import ingest


def _internet_available() -> bool:
    try:
        socket.gethostbyname("www.g2.com")
        return True
    except OSError:
        return False


def _playwright_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401

        return True
    except ImportError:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _internet_available(), reason="no internet"),
    pytest.mark.skipif(not _playwright_available(), reason="playwright not installed"),
]


def test_live_g2_one_page_smoke(session: Session) -> None:
    from playwright.sync_api import sync_playwright

    from apfun.sourcing._base import BrowserBatchClient

    src = Source(
        kind="review_sites",
        name="g2:asana-live-smoke",
        config_json={
            "site": "g2",
            "products": [{"slug": "asana", "name": "Asana"}],
            "max_pages": 1,
            "min_star": 1,
            "max_star": 3,
        },
    )
    session.add(src)
    session.flush()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        client = BrowserBatchClient(playwright=pw, browser=browser)
        try:
            result = ingest(session, src, client)
        finally:
            client.close()
    session.commit()

    if result.error_class == "blocked":
        pytest.fail(
            "G2 returned a Cloudflare block page. Per task 009 Notes + feedback 014: "
            "use scripts/import_reviews.py with a hand-exported CSV instead of "
            "escalating to stealth Playwright tactics."
        )

    assert result.items_captured >= 1, (
        f"expected ≥1 review from a real G2 page, got {result.items_captured} "
        f"(status_codes={result.status_codes}, error_class={result.error_class})"
    )
    rows = session.execute(select(RawSignal).where(RawSignal.source_id == src.id)).scalars().all()
    assert len(rows) >= 1
    for row in rows[:5]:
        payload = row.payload_json
        assert isinstance(payload, dict)
        assert payload.get("site") == "g2"
        assert isinstance(payload.get("rating"), int)
        assert payload.get("rating") in {1, 2, 3}
