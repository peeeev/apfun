"""Tests for the /ops operator dashboard (task 024).

Read-only page; tests render against a stub DB with known fixtures and assert
the six sections behave: KPI cards, scheduler STALE detection, recent runs,
sources health, LLM cost, recent errors. Plus the HTMX auto-refresh wiring.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import sessionmaker

from apfun.models import (
    Candidate,
    Decision,
    LLMRun,
    PipelineStage,
    RawSignal,
    SchedulerRun,
    SignalText,
    Source,
)


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


def _make_jobstore_table(factory: sessionmaker, rows: dict[str, float]) -> None:
    """Create the apscheduler_jobs table (not an ORM model) + insert next-run rows."""
    with factory() as s:
        s.execute(
            text(
                "CREATE TABLE IF NOT EXISTS apscheduler_jobs "
                "(id VARCHAR(191) NOT NULL PRIMARY KEY, next_run_time FLOAT, "
                "job_state BLOB NOT NULL)"
            )
        )
        for job_id, next_run in rows.items():
            s.execute(
                text(
                    "INSERT INTO apscheduler_jobs (id, next_run_time, job_state) "
                    "VALUES (:id, :nrt, :st)"
                ),
                {"id": job_id, "nrt": next_run, "st": b"x"},
            )
        s.commit()


def test_ops_page_renders_chrome_and_sections(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, _ = client_with_session
    r = client.get("/ops")
    assert r.status_code == 200
    assert "<title>ops · apfun</title>" in r.text
    # Section headers present
    for heading in ("Scheduler", "Recent runs", "Sources", "LLM cost", "Recent errors"):
        assert heading in r.text
    # Nav marks /ops active
    assert 'href="/ops"' in r.text


def test_autorefresh_wired_on_body_only(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, _ = client_with_session
    r = client.get("/ops")
    assert 'hx-get="/ops/body"' in r.text
    assert 'hx-trigger="every 30s"' in r.text


def test_ops_body_partial_is_fragment_not_full_page(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, _ = client_with_session
    r = client.get("/ops/body")
    assert r.status_code == 200
    # Partial: no <html>/chrome, but has section content.
    assert "<html" not in r.text
    assert "Scheduler" in r.text


def test_summary_cards_reflect_data(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = client_with_session
    with factory() as s:
        for i in range(3):
            s.add(
                Candidate(
                    problem_statement=f"p{i}",
                    seed_keywords_json=[],
                    dedup_key=f"k{i}",
                    decision=Decision.PENDING,
                    pipeline_stage=PipelineStage.NONE,
                )
            )
        s.add(
            LLMRun(
                task="cluster",
                model="claude-opus-4-7",
                input_tokens=1,
                output_tokens=1,
                est_cost_usd=0.05,
                attempts=1,
                ok=True,
                created_at=datetime.now(UTC),
            )
        )
        s.commit()

    body = client.get("/ops/body").text
    assert "pending candidates" in body
    # 3 pending candidates
    assert ">3<" in body
    # today's cost reflects the 0.05 run
    assert "$0.05" in body


def test_stale_warning_fires_for_past_next_run(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = client_with_session
    past = time.time() - 3600  # 1h ago
    _make_jobstore_table(factory, {"pipeline.cluster": past})

    body = client.get("/ops/body").text
    assert "⚠ STALE" in body


def test_future_next_run_is_scheduled_not_stale(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = client_with_session
    future = time.time() + 3600  # in 1h
    _make_jobstore_table(factory, {"pipeline.normalize": future})

    body = client.get("/ops/body").text
    assert "✓ scheduled" in body
    # pipeline.normalize specifically should not be flagged stale
    assert "⚠ STALE" not in body


def test_jobs_disabled_when_jobstore_absent(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    """No apscheduler_jobs table (scheduler never started) → all expected jobs
    render as disabled rather than 500-ing."""
    client, _ = client_with_session
    body = client.get("/ops/body").text
    assert "⏸ disabled" in body
    # every expected job id appears
    for job_id in ("reddit.ingest_batch", "pipeline.cluster", "pipeline.normalize"):
        assert job_id in body


def test_error_sections_empty_state(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, _ = client_with_session
    body = client.get("/ops/body").text
    assert "No errors in last 24h ✓" in body


def test_recent_errors_surface(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = client_with_session
    now = datetime.now(UTC)
    with factory() as s:
        s.add(
            SchedulerRun(
                job_id="reddit.ingest_batch",
                started_at=now,
                finished_at=now,
                ok=False,
                error="ProxyError: tunnel failed",
                items_processed=None,
            )
        )
        s.add(
            LLMRun(
                task="cluster",
                model="claude-opus-4-7",
                input_tokens=0,
                output_tokens=0,
                est_cost_usd=0.0,
                attempts=3,
                ok=False,
                error="RateLimitError: 429",
                created_at=now,
            )
        )
        s.commit()

    body = client.get("/ops/body").text
    assert "ProxyError" in body
    assert "RateLimitError" in body


def test_sources_health_marks(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = client_with_session
    with factory() as s:
        s.add(
            Source(
                kind="reddit", name="r/SaaS", config_json={}, is_active=True, consecutive_failures=0
            )
        )
        s.add(
            Source(
                kind="reddit", name="r/warn", config_json={}, is_active=True, consecutive_failures=1
            )
        )
        s.add(
            Source(
                kind="reddit",
                name="r/dead",
                config_json={},
                is_active=False,
                consecutive_failures=3,
            )
        )
        s.commit()

    body = client.get("/ops/body").text
    assert "r/SaaS" in body
    assert "r/warn" in body
    assert "r/dead" in body
    # group header shows active/disabled counts
    assert "reddit (2 active, 1 disabled)" in body


def test_unprocessed_signals_card(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    """raw_signals without a signal_text partner show as unprocessed backlog."""
    client, factory = client_with_session
    with factory() as s:
        src = Source(kind="reddit", name="r/x", config_json={})
        s.add(src)
        s.flush()
        for i in range(5):
            s.add(
                RawSignal(
                    source_id=src.id,
                    external_id=f"e{i}",
                    url="u",
                    captured_at=datetime.now(UTC),
                    content_hash=f"h{i}",
                    payload_json={},
                )
            )
        # Only 2 of the 5 are normalized.
        s.flush()
        raws = s.query(RawSignal).limit(2).all()
        for raw in raws:
            s.add(
                SignalText(
                    raw_signal_id=raw.id,
                    source_kind="reddit",
                    text="t",
                    social_proof_weight=1.0,
                    is_low_signal=False,
                    extracted_at=datetime.now(UTC),
                )
            )
        s.commit()

    body = client.get("/ops/body").text
    assert "unprocessed signals" in body
    # 5 raw - 2 normalized = 3 unprocessed
    assert ">3<" in body


def test_recent_runs_renders_items_processed_count_not_dict_method(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    """Regression: a context-dict key named `items` collides with dict.items()
    in Jinja2 attribute resolution, rendering `<built-in method items of dict>`
    instead of the integer. Caught against the live DB after the first deploy.
    """
    client, factory = client_with_session
    now = datetime.now(UTC)
    with factory() as s:
        s.add(
            SchedulerRun(
                job_id="hn.ingest_batch",
                started_at=now,
                finished_at=now,
                ok=True,
                items_processed=42,
                error=None,
            )
        )
        s.commit()

    body = client.get("/ops/body").text
    assert "<built-in method" not in body, "dict-method leaked through Jinja attribute lookup"
    # The actual count is rendered.
    assert ">42<" in body


def test_old_errors_excluded_from_24h_errors_section(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    """A 48h-old failed run still shows in 'Recent runs' (last-20, no time
    filter) but must NOT populate the 24h 'Recent errors' section — that
    section stays empty-stated."""
    client, factory = client_with_session
    old = datetime.now(UTC) - timedelta(hours=48)
    with factory() as s:
        s.add(
            SchedulerRun(
                job_id="hn.ingest_batch",
                started_at=old,
                finished_at=old,
                ok=False,
                error="OldError: ancient",
                items_processed=None,
            )
        )
        s.commit()

    body = client.get("/ops/body").text
    # It appears in Recent runs (no time filter)...
    assert "OldError: ancient" in body
    # ...but the 24h Recent-errors section is empty-stated for both subsections.
    assert body.count("No errors in last 24h ✓") == 2


# ─────────────── /ops/scheduler/restart (task 023-fix-1, request 025) ───────


def test_restart_calls_shutdown_then_start_and_logs_row(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    """Happy path: POST /ops/scheduler/restart calls scheduler.shutdown(wait=False)
    then scheduler.start(), and writes a SchedulerRun audit row.
    """
    client, factory = client_with_session
    from apfun.main import app

    stub = app.state.scheduler
    stub.shutdown_calls = 0
    stub.start_calls = 0

    r = client.post("/ops/scheduler/restart")
    assert r.status_code == 200

    assert stub.shutdown_calls == 1, "shutdown should fire exactly once"
    assert stub.start_calls == 1, "start should fire exactly once"

    with factory() as s:
        rows = (
            s.execute(select(SchedulerRun).where(SchedulerRun.job_id == "ops.manual_restart"))
            .scalars()
            .all()
        )
    assert len(rows) == 1
    row = rows[0]
    assert row.ok is True
    assert row.error is None
    assert row.items_processed is None


def test_restart_response_is_body_partial_not_full_page(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    """The endpoint returns _ops_body.html (chrome-less fragment) so HTMX can
    swap it into #ops-body. The freshly-written ops.manual_restart row appears
    in Recent runs immediately."""
    client, _ = client_with_session
    r = client.post("/ops/scheduler/restart")
    assert r.status_code == 200
    # Fragment, no chrome.
    assert "<html" not in r.text
    assert "Scheduler" in r.text
    # The just-written audit row surfaces in Recent runs.
    assert "ops.manual_restart" in r.text


def test_restart_handles_shutdown_already_stopped_gracefully(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    """shutdown() raising (e.g., SchedulerNotRunningError because the scheduler
    is already stopped) shouldn't abort the restart — proceed to start(), which
    is what the operator wanted anyway."""
    client, factory = client_with_session
    from apfun.main import app

    stub = app.state.scheduler
    stub.shutdown_calls = 0
    stub.start_calls = 0
    stub.shutdown_raises = RuntimeError("SchedulerNotRunningError: not running")

    r = client.post("/ops/scheduler/restart")
    assert r.status_code == 200

    # shutdown attempted (and raised), but start() still fired.
    assert stub.shutdown_calls == 1
    assert stub.start_calls == 1

    with factory() as s:
        row = s.execute(
            select(SchedulerRun).where(SchedulerRun.job_id == "ops.manual_restart")
        ).scalar_one()
    # The net result is "scheduler is now running" — ok=True.
    assert row.ok is True
    assert row.error is None

    # Cleanup: stop raising for any subsequent tests.
    stub.shutdown_raises = None


def test_restart_records_failure_when_start_raises(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    """If scheduler.start() itself raises, the row records ok=False with the
    error message but the response is still 200 (the user sees the dashboard
    with the failure visible in Recent runs — not a 500)."""
    client, factory = client_with_session
    from apfun.main import app

    stub = app.state.scheduler
    stub.start_raises = RuntimeError("KaboomError: jobstore unreachable")

    try:
        r = client.post("/ops/scheduler/restart")
        assert r.status_code == 200

        with factory() as s:
            row = s.execute(
                select(SchedulerRun).where(SchedulerRun.job_id == "ops.manual_restart")
            ).scalar_one()
        assert row.ok is False
        assert row.error is not None
        assert "KaboomError" in row.error
        # The failure surfaces in the rendered body (Recent errors / Recent runs).
        assert "KaboomError" in r.text
    finally:
        stub.start_raises = None


def test_restart_button_present_in_scheduler_section(
    client_with_session: tuple[TestClient, sessionmaker],
) -> None:
    """The button is wired with hx-post, hx-confirm, hx-disabled-elt, and
    targets #ops-body with innerHTML swap (innerHTML preserves the wrapper's
    30s auto-refresh trigger — outerHTML would strip it)."""
    client, _ = client_with_session
    body = client.get("/ops/body").text
    assert 'hx-post="/ops/scheduler/restart"' in body
    assert "hx-confirm=" in body
    assert 'hx-disabled-elt="this"' in body
    assert 'hx-target="#ops-body"' in body
    assert 'hx-swap="innerHTML"' in body
