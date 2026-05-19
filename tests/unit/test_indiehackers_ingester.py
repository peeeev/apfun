"""Unit tests for `apfun.sourcing.indiehackers.ingest`.

Mocks `httpx.Client` against the synthetic fixture in `tests/fixtures/indiehackers/`.
Covers both parse paths (__NEXT_DATA__ JSON and the HTML-scrape fallback) plus
the parse-failure-doesn't-raise contract.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from apfun.models import RawSignal, Source
from apfun.sourcing import _base as base_module
from apfun.sourcing import indiehackers as ih_module
from apfun.sourcing.indiehackers import ingest

_FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "indiehackers" / "grouppage_main.html"
_NEXT_DATA_STRIP_RE = re.compile(
    r'<script[^>]*id="__NEXT_DATA__"[^>]*>.*?</script>',
    re.DOTALL,
)


def _fixture_html() -> str:
    return _FIXTURE_PATH.read_text()


def _make_mock_client(status: int = 200, body: str | None = None) -> MagicMock:
    if body is None:
        body = _fixture_html()
    response = MagicMock(spec=httpx.Response)
    response.status_code = status
    response.text = body
    response.raise_for_status = MagicMock()
    client = MagicMock(spec=httpx.Client)
    client.get.return_value = response
    return client


@pytest.fixture
def ih_source(session: Session) -> Source:
    src = Source(
        kind="indiehackers",
        name="ih:main",
        config_json={"groups": ["main"], "since_hours": 24},
    )
    session.add(src)
    session.flush()
    return src


def test_ingest_parses_next_data_when_present(session: Session, ih_source: Source) -> None:
    client = _make_mock_client()
    result = ingest(session, ih_source, client=client)
    session.commit()

    # Fixture has 3 posts in __NEXT_DATA__.
    assert result.items_captured == 3
    assert result.status_codes == [200]
    assert result.error_class is None
    rows = session.execute(select(RawSignal).order_by(RawSignal.id)).scalars().all()
    assert len(rows) == 3
    slugs = {r.external_id for r in rows}
    assert "bootstrapping-a-niche-saas-to-3k-mrr" in slugs
    assert "things-that-broke-after-launch" in slugs
    assert "i-wish-someone-built-an-alternative-to" in slugs


def test_ingest_falls_back_to_html_when_next_data_missing(
    session: Session, ih_source: Source
) -> None:
    """If the __NEXT_DATA__ blob isn't there, selectolax scrapes the rendered cards."""
    html_without_blob = _NEXT_DATA_STRIP_RE.sub("", _fixture_html())
    assert "__NEXT_DATA__" not in html_without_blob
    client = _make_mock_client(body=html_without_blob)
    result = ingest(session, ih_source, client=client)
    session.commit()

    # All three rendered post cards have data-slug + class="post-card" — the
    # fallback should find them.
    assert result.items_captured == 3
    assert result.error_class is None
    rows = session.execute(select(RawSignal).order_by(RawSignal.id)).scalars().all()
    slugs = {r.external_id for r in rows}
    assert slugs == {
        "bootstrapping-a-niche-saas-to-3k-mrr",
        "things-that-broke-after-launch",
        "i-wish-someone-built-an-alternative-to",
    }


def test_parse_failure_does_not_raise(session: Session, ih_source: Source) -> None:
    """Per task 008 acceptance: parse failure surfaces in error_class, not exception."""
    client = _make_mock_client(body="<html><body>nothing useful here</body></html>")
    result = ingest(session, ih_source, client=client)
    session.commit()

    assert result.items_captured == 0
    assert result.error_class == "parse_error"
    rows = session.execute(select(RawSignal)).scalars().all()
    assert len(rows) == 0


def test_dedup_on_second_run(session: Session, ih_source: Source) -> None:
    client = _make_mock_client()
    first = ingest(session, ih_source, client=client)
    session.commit()
    assert first.items_captured == 3

    second = ingest(session, ih_source, client=client)
    session.commit()
    assert second.items_captured == 0
    rows = session.execute(select(RawSignal)).scalars().all()
    assert len(rows) == 3


def test_payload_carries_apfun_group_and_url(session: Session, ih_source: Source) -> None:
    client = _make_mock_client()
    ingest(session, ih_source, client=client)
    session.commit()

    rows = session.execute(select(RawSignal).order_by(RawSignal.id)).scalars().all()
    for row in rows:
        assert isinstance(row.payload_json, dict)
        assert row.payload_json.get("_apfun_group") == "main"
        url = row.payload_json.get("_apfun_url")
        assert isinstance(url, str)
        assert url.startswith("https://www.indiehackers.com/post/")


def test_user_agent_header_sent(session: Session, ih_source: Source) -> None:
    client = _make_mock_client()
    ingest(session, ih_source, client=client)

    _, kwargs = client.get.call_args
    assert kwargs["headers"]["User-Agent"].startswith("apfun-funnel/")


def test_rate_limiter_acquired_per_group(
    session: Session, ih_source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    acquire_count = {"n": 0}

    def fake_acquire() -> None:
        acquire_count["n"] += 1

    monkeypatch.setattr(ih_module._BUCKET, "acquire", fake_acquire)
    client = _make_mock_client()
    ingest(session, ih_source, client=client)

    assert acquire_count["n"] == 1


def test_terminal_status_returns_without_retry(
    session: Session, ih_source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(base_module.time, "sleep", lambda _s: None)
    client = _make_mock_client(status=403, body="")
    result = ingest(session, ih_source, client=client)

    assert result.status_codes == [403]
    assert result.items_captured == 0
    assert client.get.call_count == 1


def test_transient_5xx_retries_then_gives_up(
    session: Session, ih_source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ih_module.time, "sleep", lambda _s: None)
    client = _make_mock_client(status=503, body="")
    result = ingest(session, ih_source, client=client)

    assert result.status_codes == [503]
    assert client.get.call_count == base_module.MAX_RETRIES


def test_content_hash_uses_post_url() -> None:
    h_a = ih_module._content_hash("https://www.indiehackers.com/post/alpha")
    h_b = ih_module._content_hash("https://www.indiehackers.com/post/beta")
    assert h_a != h_b
    assert h_a == ih_module._content_hash("https://www.indiehackers.com/post/alpha")


def test_multi_group_config_fans_out(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    src = Source(
        kind="indiehackers",
        name="ih:multi",
        config_json={"groups": ["main", "starting-up"]},
    )
    session.add(src)
    session.flush()
    monkeypatch.setattr(ih_module._BUCKET, "acquire", lambda: None)
    client = _make_mock_client()
    result = ingest(session, src, client=client)
    # Both groups hit; dedup happens within the run too (same fixture both
    # times) — 3 unique posts inserted.
    assert client.get.call_count == 2
    assert result.items_captured == 3
    assert result.status_codes == [200, 200]
