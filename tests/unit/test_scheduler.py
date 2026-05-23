"""Unit tests for the APScheduler setup + job wrapper.

Covers:
- `build_scheduler` constructs with the expected jobstore + executor + defaults
- `_wrap` writes a `scheduler_runs` row tagged ok=False when the inner raises
- `_wrap` does NOT write a row on the happy path (inner functions self-write)
- `register_all` actually adds every prescribed job at the prescribed cadence
- `/healthz` exposes `scheduler.running` once lifespan has run

Doesn't touch the real ingesters — those have their own test suites. We just
verify the wrapper layer and the registration shape.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from apfun.models import SchedulerRun
from apfun.scheduler import jobs
from apfun.scheduler.setup import build_scheduler


@pytest.fixture
def patched_session_local(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[sessionmaker]:
    """Point `apfun.scheduler.jobs.SessionLocal` at the test engine."""
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr("apfun.scheduler.jobs.SessionLocal", factory)
    yield factory


def test_build_scheduler_uses_sqlite_jobstore_and_thread_pool(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'jobstore.db'}"
    sched = build_scheduler(db_url=db_url, pool_size=3)
    try:
        jobstore = sched._jobstores["default"]
        executor = sched._executors["default"]
        # SQLAlchemyJobStore stringifies to include the engine URL
        assert "sqlite" in str(jobstore.engine.url)
        # ThreadPoolExecutor under the hood
        assert type(executor).__name__ == "ThreadPoolExecutor"
        # job_defaults applied
        assert sched._job_defaults["coalesce"] is True
        assert sched._job_defaults["max_instances"] == 1
    finally:
        # Don't .start(); we only inspect configuration here.
        pass


def test_wrap_writes_scheduler_run_row_on_exception(
    patched_session_local: sessionmaker, engine: Engine
) -> None:
    @jobs._wrap("test.failing_job")
    def boom() -> None:
        raise RuntimeError("kaboom")

    # Wrapper swallows the exception (logged + recorded) — no re-raise.
    boom()

    with patched_session_local() as s:
        rows = (
            s.execute(select(SchedulerRun).where(SchedulerRun.job_id == "test.failing_job"))
            .scalars()
            .all()
        )
    assert len(rows) == 1
    row = rows[0]
    assert row.ok is False
    assert row.error is not None
    assert "RuntimeError" in row.error
    assert "kaboom" in row.error
    assert row.items_processed is None
    assert row.finished_at is not None
    assert row.started_at <= row.finished_at


def test_wrap_does_not_write_row_on_success(
    patched_session_local: sessionmaker,
) -> None:
    """Inner batch functions write their own scheduler_runs row on success;
    the wrapper deliberately doesn't double-write."""
    calls: list[int] = []

    @jobs._wrap("test.happy_job")
    def happy() -> None:
        calls.append(1)

    happy()
    assert calls == [1]

    with patched_session_local() as s:
        rows = (
            s.execute(select(SchedulerRun).where(SchedulerRun.job_id == "test.happy_job"))
            .scalars()
            .all()
        )
    assert rows == []


def test_register_all_adds_every_prescribed_job(tmp_path: Path) -> None:
    """Acceptance: scheduler registers every prescribed job. Verifies job IDs,
    counts, and cadence kinds (interval vs cron) to catch wiring mistakes."""
    sched = build_scheduler(db_url=f"sqlite:///{tmp_path / 'jobstore.db'}", pool_size=2)
    jobs.register_all(sched)

    by_id = {j.id: j for j in sched.get_jobs()}
    expected = {
        "reddit.ingest_batch": IntervalTrigger,
        "hn.ingest_batch": IntervalTrigger,
        "producthunt.ingest_batch": CronTrigger,
        "indiehackers.ingest_batch": CronTrigger,
        "review_sites.ingest_batch": CronTrigger,
        "pipeline.normalize": IntervalTrigger,
        "pipeline.cluster": IntervalTrigger,
    }
    assert set(by_id) == set(expected)
    for job_id, trigger_cls in expected.items():
        assert isinstance(by_id[job_id].trigger, trigger_cls), (
            f"{job_id} should use {trigger_cls.__name__}"
        )

    # Stage 2 (task 011) is deliberately NOT registered yet (per feedback 019 Q1).
    assert "stage2.demand_check" not in by_id

    # EXPECTED_JOB_IDS (consumed by the /ops dashboard to flag disabled jobs)
    # must stay in lockstep with what register_all actually schedules.
    assert set(by_id) == set(jobs.EXPECTED_JOB_IDS)

    # Cadence-spec spot checks. Reddit is every 6h.
    reddit_trigger = by_id["reddit.ingest_batch"].trigger
    assert isinstance(reddit_trigger, IntervalTrigger)
    assert reddit_trigger.interval.total_seconds() == 6 * 3600

    # Stage 1 cluster is every 2h.
    cluster_trigger = by_id["pipeline.cluster"].trigger
    assert isinstance(cluster_trigger, IntervalTrigger)
    assert cluster_trigger.interval.total_seconds() == 2 * 3600


def test_interval_jobs_are_grid_anchored_not_float() -> None:
    """Every interval job must carry an explicit start_date so its cadence
    anchors to a fixed UTC grid, not to scheduler-start time. A bare
    IntervalTrigger (start_date=None) drifts relative to the others depending
    on when the process booted — which is the ordering bug this guards against.
    """
    from apscheduler.triggers.interval import IntervalTrigger as IT

    for job_id in (
        "reddit.ingest_batch",
        "hn.ingest_batch",
        "pipeline.normalize",
        "pipeline.cluster",
    ):
        sched = build_scheduler(db_url="sqlite://", pool_size=1)
        jobs.register_all(sched)
        trigger = {j.id: j for j in sched.get_jobs()}[job_id].trigger
        assert isinstance(trigger, IT)
        assert trigger.start_date is not None, f"{job_id} interval trigger must be grid-anchored"


def test_normalize_fires_before_cluster_each_window() -> None:
    """Within a 2h window, normalize (:00) must fire ahead of cluster (:10) so
    signal_text rows are fresh when Stage 1 reads them. Regression guard for
    the inverted-ordering bug where cluster was anchored but normalize floated.
    """
    from datetime import UTC, datetime

    sched = build_scheduler(db_url="sqlite://", pool_size=1)
    jobs.register_all(sched)
    by_id = {j.id: j for j in sched.get_jobs()}
    norm = by_id["pipeline.normalize"].trigger
    clus = by_id["pipeline.cluster"].trigger

    # Reference just before an even-hour boundary → both fire in the same window.
    ref = datetime(2026, 6, 1, 1, 59, 0, tzinfo=UTC)
    norm_next = norm.get_next_fire_time(None, ref)
    clus_next = clus.get_next_fire_time(None, ref)
    assert norm_next == datetime(2026, 6, 1, 2, 0, 0, tzinfo=UTC)
    assert clus_next == datetime(2026, 6, 1, 2, 10, 0, tzinfo=UTC)
    assert norm_next < clus_next


def test_hn_ingest_offset_one_hour_after_reddit() -> None:
    """HN is anchored 1h after Reddit so the two heaviest ingesters don't
    collide. Regression guard: Reddit used to float (bare trigger)."""
    from datetime import UTC, datetime, timedelta

    sched = build_scheduler(db_url="sqlite://", pool_size=1)
    jobs.register_all(sched)
    by_id = {j.id: j for j in sched.get_jobs()}
    # Reference just before the 06:00 reddit grid point → both fire in the same
    # 6h window, hn exactly 1h after reddit.
    ref = datetime(2026, 6, 1, 5, 59, 0, tzinfo=UTC)
    reddit_next = by_id["reddit.ingest_batch"].trigger.get_next_fire_time(None, ref)
    hn_next = by_id["hn.ingest_batch"].trigger.get_next_fire_time(None, ref)
    assert reddit_next == datetime(2026, 6, 1, 6, 0, 0, tzinfo=UTC)
    assert hn_next == datetime(2026, 6, 1, 7, 0, 0, tzinfo=UTC)
    assert hn_next - reddit_next == timedelta(hours=1)


def test_healthz_reports_scheduler_running(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """The autouse `_stub_scheduler` fixture (conftest) sets running=True; the
    /healthz endpoint surfaces that on app.state.scheduler."""
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr("apfun.db.SessionLocal", factory)
    monkeypatch.setattr("apfun.web.routes.inbox.SessionLocal", factory)

    from apfun.main import app

    with TestClient(app) as c:
        r = c.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "scheduler": {"running": True}}


def test_active_sources_filters_by_kind_and_is_active(
    patched_session_local: sessionmaker,
) -> None:
    """`_active_sources('hn')` should pick up only active HN sources."""
    from apfun.models import Source

    with patched_session_local() as s:
        s.add(Source(kind="hn", name="hn:active", config_json={}, is_active=True))
        s.add(Source(kind="hn", name="hn:disabled", config_json={}, is_active=False))
        s.add(Source(kind="reddit", name="r:active", config_json={}, is_active=True))
        s.commit()

    result = jobs._active_sources("hn")
    names = {src.name for src in result}
    assert names == {"hn:active"}


def test_failed_job_writes_row_with_timing_invariant(
    patched_session_local: sessionmaker,
) -> None:
    """started_at <= finished_at, both bracketed by call wall-clock window.

    SQLite reads `DateTime(timezone=True)` columns back as naive, so compare
    against naive UTC bounds.
    """

    @jobs._wrap("test.timing_check")
    def fails_fast() -> None:
        raise ValueError("nope")

    before = datetime.now(UTC).replace(tzinfo=None)
    fails_fast()
    after = datetime.now(UTC).replace(tzinfo=None)

    with patched_session_local() as s:
        row = s.execute(
            select(SchedulerRun).where(SchedulerRun.job_id == "test.timing_check")
        ).scalar_one()
    started = row.started_at.replace(tzinfo=None) if row.started_at.tzinfo else row.started_at
    finished = (
        row.finished_at.replace(tzinfo=None)
        if row.finished_at and row.finished_at.tzinfo
        else row.finished_at
    )
    assert finished is not None
    assert before <= started <= finished <= after


def test_scheduler_shutdown_via_lifespan(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """When `TestClient.__exit__` triggers shutdown, the stub scheduler's
    `running` flag flips to False — verifying the lifespan handler calls
    `shutdown(wait=False)` correctly."""
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr("apfun.db.SessionLocal", factory)
    monkeypatch.setattr("apfun.web.routes.inbox.SessionLocal", factory)

    from apfun.main import app

    with TestClient(app):
        scheduler_during = app.state.scheduler
        assert scheduler_during.running is True
    # After context exit, lifespan tore down — `shutdown` ran.
    assert scheduler_during.running is False
