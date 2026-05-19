"""Batch-wrapper tests for `apfun.sourcing.hn.ingest_batch`.

Mirrors `tests/unit/test_reddit_ingest_batch.py` minus the UA-block guard
(HN doesn't UA-block). Verifies counter increments on terminal statuses,
reset on success, transient-no-increment, three-strikes auto-disable, and
the `scheduler_runs` row write.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from apfun.models import SchedulerRun, Source
from apfun.sourcing.hn import IngestResult, ingest_batch


def _make_source(session: Session, name: str) -> Source:
    src = Source(
        kind="hn",
        name=name,
        config_json={"queries": ["tool you wish existed"]},
    )
    session.add(src)
    session.flush()
    return src


@pytest.fixture
def three_sources(session: Session) -> list[Source]:
    sources = [_make_source(session, f"hn:q_{i}") for i in range(3)]
    session.commit()
    return sources


def _patch_ingest_with(results_by_source: dict[int, IngestResult]) -> Any:
    def fake_ingest(_session: Session, source: Source, *_args: Any, **_kwargs: Any) -> IngestResult:
        return results_by_source[source.id]

    return patch("apfun.sourcing.hn.ingest", side_effect=fake_ingest)


def test_success_resets_counter(session: Session, three_sources: list[Source]) -> None:
    three_sources[0].consecutive_failures = 2
    session.commit()
    canned = {
        s.id: IngestResult(source_id=s.id, items_captured=3, status_codes=[200])
        for s in three_sources
    }
    with _patch_ingest_with(canned):
        ingest_batch(session, three_sources, client=MagicMock())
    session.refresh(three_sources[0])
    assert three_sources[0].consecutive_failures == 0
    assert three_sources[0].is_active is True


def test_terminal_status_increments_counter(session: Session, three_sources: list[Source]) -> None:
    canned: dict[int, IngestResult] = {
        three_sources[0].id: IngestResult(
            source_id=three_sources[0].id, items_captured=0, status_codes=[400]
        ),
    }
    for s in three_sources[1:]:
        canned[s.id] = IngestResult(source_id=s.id, items_captured=1, status_codes=[200])
    with _patch_ingest_with(canned):
        ingest_batch(session, three_sources, client=MagicMock())
    session.refresh(three_sources[0])
    assert three_sources[0].consecutive_failures == 1
    assert three_sources[0].is_active is True


def test_three_strikes_auto_disable(session: Session, three_sources: list[Source]) -> None:
    three_sources[0].consecutive_failures = 2
    session.commit()
    canned: dict[int, IngestResult] = {
        three_sources[0].id: IngestResult(
            source_id=three_sources[0].id, items_captured=0, status_codes=[404]
        ),
    }
    for s in three_sources[1:]:
        canned[s.id] = IngestResult(source_id=s.id, items_captured=1, status_codes=[200])
    with _patch_ingest_with(canned):
        ingest_batch(session, three_sources, client=MagicMock())
    session.refresh(three_sources[0])
    assert three_sources[0].consecutive_failures == 3
    assert three_sources[0].is_active is False


def test_transient_5xx_does_not_increment(session: Session, three_sources: list[Source]) -> None:
    three_sources[0].consecutive_failures = 0
    session.commit()
    canned: dict[int, IngestResult] = {
        three_sources[0].id: IngestResult(
            source_id=three_sources[0].id,
            items_captured=0,
            status_codes=[503],
            error_class="HTTP 503",
        ),
    }
    for s in three_sources[1:]:
        canned[s.id] = IngestResult(source_id=s.id, items_captured=1, status_codes=[200])
    with _patch_ingest_with(canned):
        ingest_batch(session, three_sources, client=MagicMock())
    session.refresh(three_sources[0])
    assert three_sources[0].consecutive_failures == 0
    assert three_sources[0].is_active is True


def test_scheduler_run_row_written(session: Session, three_sources: list[Source]) -> None:
    canned = {
        s.id: IngestResult(source_id=s.id, items_captured=2, status_codes=[200])
        for s in three_sources
    }
    with _patch_ingest_with(canned):
        ingest_batch(session, three_sources, client=MagicMock(), job_id="test.hn_batch")

    rows = (
        session.execute(select(SchedulerRun).where(SchedulerRun.job_id == "test.hn_batch"))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].ok is True
    assert rows[0].items_processed == 6  # 3 sources × 2 items
