"""Tests for /ops scheduler pause/resume (task 014-fix-2, request 031 §1).

Mirrors the restart-endpoint tests: a stubbed scheduler on `app.state`
(`pause()`/`resume()`/`state` from the conftest stub), assertions on the audit
rows, the persisted runtime_state flag, and the rendered status indicator.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import sessionmaker

from apfun.models import SCHEDULER_PAUSED_KEY, RuntimeState, SchedulerRun
from apfun.scheduler.pause_state import is_scheduler_paused


@pytest.fixture
def client_with_session(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, sessionmaker]]:
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr("apfun.db.SessionLocal", factory)
    monkeypatch.setattr("apfun.web.routes.ops.SessionLocal", factory)
    monkeypatch.setattr("apfun.web.routes.inbox.SessionLocal", factory)
    from apfun.main import app

    with TestClient(app) as c:
        yield c, factory


def test_pause_calls_pause_logs_row_and_persists_flag(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = client_with_session
    from apfun.main import app

    stub = app.state.scheduler
    stub.pause_calls = 0

    r = client.post("/ops/scheduler/pause")
    assert r.status_code == 200
    assert stub.pause_calls == 1

    with factory() as s:
        row = s.execute(
            select(SchedulerRun).where(SchedulerRun.job_id == "ops.manual_pause")
        ).scalar_one()
        assert row.ok is True
        assert row.error is None
        assert is_scheduler_paused(s) is True


def test_resume_calls_resume_logs_row_and_clears_flag(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = client_with_session
    from apfun.main import app

    stub = app.state.scheduler
    stub.resume_calls = 0

    # Pause first so there's a flag to clear.
    client.post("/ops/scheduler/pause")
    r = client.post("/ops/scheduler/resume")
    assert r.status_code == 200
    assert stub.resume_calls == 1

    with factory() as s:
        row = s.execute(
            select(SchedulerRun).where(SchedulerRun.job_id == "ops.manual_resume")
        ).scalar_one()
        assert row.ok is True
        assert is_scheduler_paused(s) is False
        # The flag row was deleted, not left as false.
        assert s.get(RuntimeState, SCHEDULER_PAUSED_KEY) is None


def test_status_indicator_and_buttons_reflect_state(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, _ = client_with_session
    from apfun.main import app

    app.state.scheduler.state = 1  # running
    body = client.get("/ops/body").text
    assert "running" in body
    assert 'hx-post="/ops/scheduler/pause"' in body  # "stop" button shown when running

    client.post("/ops/scheduler/pause")
    body = client.get("/ops/body").text
    assert "paused" in body
    assert 'hx-post="/ops/scheduler/resume"' in body  # "resume" button shown when paused


def test_pause_records_failure_when_scheduler_raises(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = client_with_session
    from apfun.main import app

    stub = app.state.scheduler
    stub.pause_raises = RuntimeError("SchedulerNotRunningError")
    try:
        r = client.post("/ops/scheduler/pause")
        assert r.status_code == 200  # surfaced in the dashboard, not a 500
        with factory() as s:
            row = (
                s.execute(select(SchedulerRun).where(SchedulerRun.job_id == "ops.manual_pause"))
                .scalars()
                .all()[-1]
            )
            assert row.ok is False
            assert "SchedulerNotRunningError" in (row.error or "")
            # The flag is NOT set when pause() failed.
            assert is_scheduler_paused(s) is False
    finally:
        stub.pause_raises = None


def test_paused_scheduler_marks_jobs_paused_not_scheduled(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    """A paused scheduler must not render jobs as '✓ scheduled' (pause() freezes
    next_run_time, so they won't fire and would otherwise drift into false STALE).
    Per operator report."""
    import time

    from sqlalchemy import text

    from apfun.scheduler.jobs import EXPECTED_JOB_IDS

    client, factory = client_with_session
    job_id = next(iter(EXPECTED_JOB_IDS))
    with factory() as s:
        s.execute(
            text(
                "CREATE TABLE IF NOT EXISTS apscheduler_jobs (id VARCHAR(191) NOT NULL "
                "PRIMARY KEY, next_run_time FLOAT, job_state BLOB NOT NULL)"
            )
        )
        s.execute(
            text(
                "INSERT INTO apscheduler_jobs (id, next_run_time, job_state) "
                "VALUES (:id, :nrt, :st)"
            ),
            {"id": job_id, "nrt": time.time() + 3600, "st": b"x"},
        )
        s.commit()

    # Running: the seeded job reads as scheduled.
    assert "✓ scheduled" in client.get("/ops/body").text

    # Paused: the job flips to "paused", a frozen-times note appears, and nothing
    # claims to be scheduled.
    client.post("/ops/scheduler/pause")
    body = client.get("/ops/body").text
    assert "⏸ paused" in body
    assert "frozen" in body
    assert "✓ scheduled" not in body
