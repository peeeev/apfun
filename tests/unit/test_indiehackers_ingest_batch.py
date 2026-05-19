"""Batch-wrapper tests for `apfun.sourcing.indiehackers.ingest_batch`.

Mirrors HN's batch tests — no UA-block guard, no missing-token branch. The
one IH-specific behavior worth pinning: `error_class="parse_error"` (which is
NOT in `status_codes`) should NOT increment the per-source counter, because
the source itself was reachable; the parse failure is layout-drift on our side.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from apfun.models import SchedulerRun, Source
from apfun.sourcing.indiehackers import IngestResult, ingest_batch


def _make_source(session: Session, name: str) -> Source:
    src = Source(
        kind="indiehackers",
        name=name,
        config_json={"groups": [name.split(":", 1)[-1]]},
    )
    session.add(src)
    session.flush()
    return src


@pytest.fixture
def three_sources(session: Session) -> list[Source]:
    sources = [_make_source(session, f"ih:group_{i}") for i in range(3)]
    session.commit()
    return sources


def _patch_ingest_with(results_by_source: dict[int, IngestResult]) -> Any:
    def fake_ingest(_session: Session, source: Source, *_args: Any, **_kwargs: Any) -> IngestResult:
        return results_by_source[source.id]

    return patch("apfun.sourcing.indiehackers.ingest", side_effect=fake_ingest)


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


def test_terminal_status_increments(session: Session, three_sources: list[Source]) -> None:
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
    assert three_sources[0].consecutive_failures == 1


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


def test_parse_failure_does_not_increment_counter(
    session: Session, three_sources: list[Source]
) -> None:
    """`error_class='parse_error'` with a 200 status: source is reachable; counter unchanged."""
    three_sources[0].consecutive_failures = 0
    session.commit()
    canned: dict[int, IngestResult] = {
        three_sources[0].id: IngestResult(
            source_id=three_sources[0].id,
            items_captured=0,
            status_codes=[200],
            error_class="parse_error",
        ),
    }
    for s in three_sources[1:]:
        canned[s.id] = IngestResult(source_id=s.id, items_captured=1, status_codes=[200])
    with _patch_ingest_with(canned):
        ingest_batch(session, three_sources, client=MagicMock())
    session.refresh(three_sources[0])
    # 200 is success — counter resets to 0 (was already 0).
    assert three_sources[0].consecutive_failures == 0


def test_scheduler_run_row_written(session: Session, three_sources: list[Source]) -> None:
    canned = {
        s.id: IngestResult(source_id=s.id, items_captured=2, status_codes=[200])
        for s in three_sources
    }
    with _patch_ingest_with(canned):
        ingest_batch(session, three_sources, client=MagicMock(), job_id="test.ih_batch")

    rows = (
        session.execute(select(SchedulerRun).where(SchedulerRun.job_id == "test.ih_batch"))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].ok is True
    assert rows[0].items_processed == 6  # 3 sources × 2 items
