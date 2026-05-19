"""Batch-wrapper tests for `apfun.sourcing.producthunt.ingest_batch`.

Mirrors the Reddit/HN batch tests with one addition: the missing-token result
must NOT increment per-source counters (it's an operator config issue, not a
runtime fault). The batch still writes an `ok=True / items_processed=0`
scheduler_runs row in that case — see task 007 spec.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from apfun.models import SchedulerRun, Source
from apfun.sourcing.producthunt import IngestResult, ingest_batch


def _make_source(session: Session, name: str) -> Source:
    src = Source(
        kind="producthunt",
        name=name,
        config_json={"surface": "topic", "topics": ["developer-tools"]},
    )
    session.add(src)
    session.flush()
    return src


@pytest.fixture
def three_sources(session: Session) -> list[Source]:
    sources = [_make_source(session, f"ph:topic_{i}") for i in range(3)]
    session.commit()
    return sources


def _patch_ingest_with(results_by_source: dict[int, IngestResult]) -> Any:
    def fake_ingest(_session: Session, source: Source, *_args: Any, **_kwargs: Any) -> IngestResult:
        return results_by_source[source.id]

    return patch("apfun.sourcing.producthunt.ingest", side_effect=fake_ingest)


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
            source_id=three_sources[0].id, items_captured=0, status_codes=[401]
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
            source_id=three_sources[0].id, items_captured=0, status_codes=[403]
        ),
    }
    for s in three_sources[1:]:
        canned[s.id] = IngestResult(source_id=s.id, items_captured=1, status_codes=[200])
    with _patch_ingest_with(canned):
        ingest_batch(session, three_sources, client=MagicMock())
    session.refresh(three_sources[0])
    assert three_sources[0].consecutive_failures == 3
    assert three_sources[0].is_active is False


def test_missing_token_does_not_increment_counter(
    session: Session, three_sources: list[Source]
) -> None:
    """The 'feature 007 spec' invariant: missing token is operator config issue."""
    three_sources[0].consecutive_failures = 0
    session.commit()
    canned: dict[int, IngestResult] = {
        three_sources[0].id: IngestResult(
            source_id=three_sources[0].id,
            items_captured=0,
            status_codes=[],
            error_class="missing_token",
        ),
    }
    for s in three_sources[1:]:
        canned[s.id] = IngestResult(source_id=s.id, items_captured=1, status_codes=[200])
    with _patch_ingest_with(canned):
        ingest_batch(session, three_sources, client=MagicMock())

    session.refresh(three_sources[0])
    assert three_sources[0].consecutive_failures == 0
    assert three_sources[0].is_active is True


def test_missing_token_batch_writes_ok_scheduler_run(
    session: Session, three_sources: list[Source]
) -> None:
    """All-missing-token batch: ok=True, items_processed=0 — scheduler keeps marching."""
    canned: dict[int, IngestResult] = {
        s.id: IngestResult(
            source_id=s.id, items_captured=0, status_codes=[], error_class="missing_token"
        )
        for s in three_sources
    }
    with _patch_ingest_with(canned):
        ingest_batch(session, three_sources, client=MagicMock(), job_id="test.ph_no_token")

    rows = (
        session.execute(select(SchedulerRun).where(SchedulerRun.job_id == "test.ph_no_token"))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].ok is True
    assert rows[0].items_processed == 0
    assert rows[0].error is None


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
        ingest_batch(session, three_sources, client=MagicMock(), job_id="test.ph_batch")

    rows = (
        session.execute(select(SchedulerRun).where(SchedulerRun.job_id == "test.ph_batch"))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].ok is True
    assert rows[0].items_processed == 6
