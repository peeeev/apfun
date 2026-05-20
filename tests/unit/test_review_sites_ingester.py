"""Unit tests for `apfun.sourcing.review_sites._common.ingest` + `ingest_batch`.

Mocks the per-site `fetch_reviews` adapter so we can exercise the dispatch +
dedup + block-marker + bad-config paths without launching a real browser.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from apfun.models import RawSignal, SchedulerRun, Source
from apfun.sourcing._base import BrowserBatchClient
from apfun.sourcing.review_sites._common import (
    BlockedError,
    ReviewDict,
    detect_block,
    ingest,
    ingest_batch,
    review_content_hash,
)


def _review(
    *,
    site: str = "g2",
    slug: str = "asana",
    rid: str | None = "rid-1",
    rating: int = 2,
    body: str = "review body",
) -> ReviewDict:
    return {
        "site": site,
        "product_slug": slug,
        "product_name": slug.title(),
        "review_id": rid,
        "title": "t",
        "body": body,
        "rating": rating,
        "author": "a",
        "posted_at": "2026-04-12T10:00:00Z",
        "helpful_count": 5,
        "permalink": f"https://www.{site}.com/p/{slug}/reviews/{rid or 'x'}",
    }


def _fake_client() -> MagicMock:
    """Return a BrowserBatchClient-shaped MagicMock with a usable .new_context()."""
    client = MagicMock(spec=BrowserBatchClient)
    context = MagicMock()
    client.new_context.return_value = context
    return client


@pytest.fixture
def g2_source(session: Session) -> Source:
    src = Source(
        kind="review_sites",
        name="g2:asana",
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
    return src


def test_ingest_inserts_reviews(session: Session, g2_source: Source) -> None:
    fake_reviews = [_review(rid="r1"), _review(rid="r2"), _review(rid="r3")]
    with patch("apfun.sourcing.review_sites.g2.fetch_reviews", return_value=fake_reviews):
        result = ingest(session, g2_source, _fake_client())
    session.commit()

    assert result.items_captured == 3
    assert result.status_codes == [200]
    assert result.error_class is None
    rows = session.execute(select(RawSignal)).scalars().all()
    assert len(rows) == 3
    for row in rows:
        assert row.payload_json["site"] == "g2"  # type: ignore[index]


def test_ingest_dedup_on_second_run(session: Session, g2_source: Source) -> None:
    fake_reviews = [_review(rid="r1"), _review(rid="r2")]
    with patch("apfun.sourcing.review_sites.g2.fetch_reviews", return_value=fake_reviews):
        first = ingest(session, g2_source, _fake_client())
        session.commit()
        second = ingest(session, g2_source, _fake_client())
        session.commit()
    assert first.items_captured == 2
    assert second.items_captured == 0
    rows = session.execute(select(RawSignal)).scalars().all()
    assert len(rows) == 2


def test_blocked_marker_surfaces_as_terminal(session: Session, g2_source: Source) -> None:
    def raise_blocked(*_args: Any, **_kwargs: Any) -> list[ReviewDict]:
        raise BlockedError("cf-browser-verification")

    with patch("apfun.sourcing.review_sites.g2.fetch_reviews", side_effect=raise_blocked):
        result = ingest(session, g2_source, _fake_client())
    session.commit()

    assert result.items_captured == 0
    assert result.error_class == "blocked"
    assert result.status_codes == [403]


def test_unknown_site_in_config(session: Session) -> None:
    src = Source(
        kind="review_sites",
        name="bogus:x",
        config_json={"site": "yelp", "products": [{"slug": "anything"}]},
    )
    session.add(src)
    session.flush()

    result = ingest(session, src, _fake_client())
    session.commit()
    assert result.items_captured == 0
    assert result.error_class == "unknown_site"
    assert result.status_codes == []


def test_bad_config_site(session: Session) -> None:
    src = Source(
        kind="review_sites",
        name="bad",
        config_json={"products": [{"slug": "anything"}]},  # no `site` key
    )
    session.add(src)
    session.flush()

    result = ingest(session, src, _fake_client())
    session.commit()
    assert result.items_captured == 0
    assert result.error_class == "bad_config_site"


def test_empty_products_returns_zero(session: Session) -> None:
    src = Source(
        kind="review_sites",
        name="empty",
        config_json={"site": "g2", "products": []},
    )
    session.add(src)
    session.flush()

    result = ingest(session, src, _fake_client())
    session.commit()
    assert result.items_captured == 0
    assert result.error_class is None


def test_detect_block_matches_markers() -> None:
    assert detect_block("<html>Just a moment...</html>") == "Just a moment"
    assert detect_block("<title>Access denied</title>") == "Access denied"
    assert detect_block("<p>nothing to see</p>") is None


def test_review_content_hash_uses_review_id_when_present() -> None:
    h_with = review_content_hash(
        "g2", "asana", "rid-123", rating=2, posted_at="x", author="a", body="b"
    )
    # Body change must not affect hash when review_id is present.
    h_with_diff_body = review_content_hash(
        "g2", "asana", "rid-123", rating=2, posted_at="x", author="a", body="DIFFERENT"
    )
    assert h_with == h_with_diff_body


def test_review_content_hash_falls_back_on_missing_id() -> None:
    """When review_id is absent, body becomes part of the hash."""
    h_a = review_content_hash("g2", "asana", None, rating=2, posted_at="x", author="a", body="aaa")
    h_b = review_content_hash("g2", "asana", None, rating=2, posted_at="x", author="a", body="bbb")
    assert h_a != h_b


def _patch_ingest_with(results_by_source: dict[int, Any]) -> Any:
    from apfun.sourcing._base import IngestResult

    def fake_ingest(_session: Session, source: Source, *_args: Any, **_kwargs: Any) -> IngestResult:
        return results_by_source[source.id]

    return patch("apfun.sourcing.review_sites._common.ingest", side_effect=fake_ingest)


def test_ingest_batch_scheduler_run_and_health_updates(session: Session) -> None:
    from apfun.sourcing._base import IngestResult

    sources: list[Source] = []
    for i in range(3):
        s = Source(
            kind="review_sites",
            name=f"g2:product_{i}",
            config_json={"site": "g2", "products": [{"slug": f"p{i}"}]},
        )
        session.add(s)
        session.flush()
        sources.append(s)
    session.commit()

    canned = {
        sources[0].id: IngestResult(source_id=sources[0].id, items_captured=4, status_codes=[200]),
        sources[1].id: IngestResult(source_id=sources[1].id, items_captured=0, status_codes=[403]),
        sources[2].id: IngestResult(source_id=sources[2].id, items_captured=2, status_codes=[200]),
    }
    with _patch_ingest_with(canned):
        ingest_batch(session, sources, job_id="test.rs_batch", client=_fake_client())

    session.refresh(sources[1])
    assert sources[1].consecutive_failures == 1  # 403 increments
    assert sources[0].consecutive_failures == 0  # 200 resets

    rows = (
        session.execute(select(SchedulerRun).where(SchedulerRun.job_id == "test.rs_batch"))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].ok is True
    assert rows[0].items_processed == 6


def test_bad_config_does_not_increment_counter(session: Session) -> None:
    from apfun.sourcing._base import IngestResult

    s = Source(kind="review_sites", name="bc", config_json={})
    session.add(s)
    session.flush()
    s.consecutive_failures = 0
    session.commit()

    canned = {
        s.id: IngestResult(
            source_id=s.id, items_captured=0, status_codes=[], error_class="bad_config_site"
        ),
    }
    with _patch_ingest_with(canned):
        ingest_batch(session, [s], job_id="test.rs_bad", client=_fake_client())
    session.refresh(s)
    assert s.consecutive_failures == 0
    assert s.is_active is True
