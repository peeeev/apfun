"""Batch-wrapper tests for `apfun.sourcing.reddit.ingest_batch`.

Verifies the scheduler-smart layer: per-source counter increments on terminal
statuses, reset on success, UA-block guard when >50% of sources return 403,
auto-disable after three strikes, and `scheduler_runs` row write.

These exercise `ingest_batch` directly with stubbed-out per-source `ingest`
behavior — see also `test_reddit_ingester.py` for the per-source path.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from apfun.models import SchedulerRun, Source
from apfun.sourcing.reddit import IngestResult, ingest_batch


def _make_source(session: Session, name: str) -> Source:
    src = Source(
        kind="reddit",
        name=name,
        config_json={"subreddits": [name.replace("r/", "")]},
    )
    session.add(src)
    session.flush()
    return src


@pytest.fixture
def four_sources(session: Session) -> list[Source]:
    sources = [_make_source(session, f"r/sub_{i}") for i in range(4)]
    session.commit()
    return sources


def _patch_ingest_with(results_by_source: dict[int, IngestResult]) -> Any:
    """Patch reddit.ingest to return canned per-source results, keyed by source.id."""

    def fake_ingest(_session: Session, source: Source, **_kwargs: Any) -> IngestResult:
        return results_by_source[source.id]

    return patch("apfun.sourcing.reddit.ingest", side_effect=fake_ingest)


def test_success_resets_counter(session: Session, four_sources: list[Source]) -> None:
    # Seed a non-zero counter to confirm reset.
    four_sources[0].consecutive_failures = 2
    session.commit()
    canned = {
        s.id: IngestResult(source_id=s.id, items_captured=3, status_codes=[200])
        for s in four_sources
    }
    with _patch_ingest_with(canned):
        ingest_batch(session, four_sources, client=MagicMock())
    session.refresh(four_sources[0])
    assert four_sources[0].consecutive_failures == 0
    assert four_sources[0].is_active is True


def test_terminal_status_increments_counter(session: Session, four_sources: list[Source]) -> None:
    canned: dict[int, IngestResult] = {}
    # First source gets a 404; rest succeed (so batch isn't above UA-block fraction).
    canned[four_sources[0].id] = IngestResult(
        source_id=four_sources[0].id, items_captured=0, status_codes=[404]
    )
    for s in four_sources[1:]:
        canned[s.id] = IngestResult(source_id=s.id, items_captured=1, status_codes=[200])
    with _patch_ingest_with(canned):
        ingest_batch(session, four_sources, client=MagicMock())

    session.refresh(four_sources[0])
    assert four_sources[0].consecutive_failures == 1
    assert four_sources[0].is_active is True


def test_three_strikes_auto_disable(session: Session, four_sources: list[Source]) -> None:
    # Pre-seed counter to 2; next 410 should hit the threshold.
    four_sources[0].consecutive_failures = 2
    session.commit()

    canned: dict[int, IngestResult] = {
        four_sources[0].id: IngestResult(
            source_id=four_sources[0].id, items_captured=0, status_codes=[410]
        ),
    }
    for s in four_sources[1:]:
        canned[s.id] = IngestResult(source_id=s.id, items_captured=1, status_codes=[200])
    with _patch_ingest_with(canned):
        ingest_batch(session, four_sources, client=MagicMock())

    session.refresh(four_sources[0])
    assert four_sources[0].consecutive_failures == 3
    assert four_sources[0].is_active is False


def test_transient_5xx_does_not_increment(session: Session, four_sources: list[Source]) -> None:
    """5xx and 429 are about us or Reddit, not about the sub being dead."""
    four_sources[0].consecutive_failures = 0
    session.commit()
    canned: dict[int, IngestResult] = {
        four_sources[0].id: IngestResult(
            source_id=four_sources[0].id,
            items_captured=0,
            status_codes=[503],
            error_class="HTTP 503",
        ),
    }
    for s in four_sources[1:]:
        canned[s.id] = IngestResult(source_id=s.id, items_captured=1, status_codes=[200])
    with _patch_ingest_with(canned):
        ingest_batch(session, four_sources, client=MagicMock())

    session.refresh(four_sources[0])
    assert four_sources[0].consecutive_failures == 0
    assert four_sources[0].is_active is True


def test_ua_block_guard_suppresses_counter_increments(
    session: Session, four_sources: list[Source]
) -> None:
    """>50% 403s in one batch → treat as global UA-block, don't touch counters."""
    # 3 out of 4 sources return 403 → 75% > 50% → UA block triggered.
    canned: dict[int, IngestResult] = {
        four_sources[i].id: IngestResult(
            source_id=four_sources[i].id, items_captured=0, status_codes=[403]
        )
        for i in range(3)
    }
    canned[four_sources[3].id] = IngestResult(
        source_id=four_sources[3].id, items_captured=2, status_codes=[200]
    )
    with _patch_ingest_with(canned):
        ingest_batch(session, four_sources, client=MagicMock())

    # No per-source counter increments even though three sources saw 403.
    for s in four_sources[:3]:
        session.refresh(s)
        assert s.consecutive_failures == 0, (
            f"UA-block guard should suppress increments; got {s.consecutive_failures}"
        )
        assert s.is_active is True


def test_ua_block_only_triggers_above_threshold(
    session: Session, four_sources: list[Source]
) -> None:
    """At exactly 50% 403s, the UA-block guard should NOT trigger (strictly >)."""
    canned: dict[int, IngestResult] = {
        four_sources[0].id: IngestResult(
            source_id=four_sources[0].id, items_captured=0, status_codes=[403]
        ),
        four_sources[1].id: IngestResult(
            source_id=four_sources[1].id, items_captured=0, status_codes=[403]
        ),
        four_sources[2].id: IngestResult(
            source_id=four_sources[2].id, items_captured=1, status_codes=[200]
        ),
        four_sources[3].id: IngestResult(
            source_id=four_sources[3].id, items_captured=1, status_codes=[200]
        ),
    }
    with _patch_ingest_with(canned):
        ingest_batch(session, four_sources, client=MagicMock())

    # 50% exactly is NOT >50% → counters DO increment for the 403 sources.
    for s in four_sources[:2]:
        session.refresh(s)
        assert s.consecutive_failures == 1


def test_scheduler_run_row_written(session: Session, four_sources: list[Source]) -> None:
    canned = {
        s.id: IngestResult(source_id=s.id, items_captured=2, status_codes=[200])
        for s in four_sources
    }
    with _patch_ingest_with(canned):
        ingest_batch(session, four_sources, client=MagicMock(), job_id="test.reddit_batch")

    rows = (
        session.execute(select(SchedulerRun).where(SchedulerRun.job_id == "test.reddit_batch"))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].ok is True
    assert rows[0].items_processed == 8  # 4 sources × 2 items each
