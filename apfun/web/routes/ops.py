"""`/ops` — read-only operator dashboard (task 024).

A single server-rendered page summarizing funnel health: KPI cards, the
scheduler job calendar (with STALE detection), recent scheduler runs, source
health, LLM cost breakdown, and recent errors. No mutations, no LLM calls —
every figure comes from the local SQLite DB.

Two routes share one data collector:
- `GET /ops`       — full page (chrome + body)
- `GET /ops/body`  — just the data area, polled every 30s via HTMX

Auth is handled at the edge (Apache htpasswd over apfun.online); this app does
not look at `Authorization` headers. Per `docs/orchestrator/023-ops-dashboard.md`.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from starlette.requests import Request

from apfun.db import SessionLocal
from apfun.models import Candidate, Decision, LLMRun, RawSignal, SchedulerRun, SignalText, Source
from apfun.scheduler.jobs import EXPECTED_JOB_IDS

logger = logging.getLogger(__name__)

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# heuristic 2026-05-23 — a job whose next_run_time is more than this far in the
# past is "stale": the scheduler should have fired it. Small enough to catch a
# wedged job quickly, large enough to not false-positive on a job mid-run or a
# few seconds of clock skew. Per orchestrator request 023 §2.
_STALE_GRACE = timedelta(minutes=5)


def _session() -> Iterator[Session]:
    """FastAPI dependency: yield a sync Session, close on exit."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _fmt_rel(dt: datetime | None, *, now: datetime) -> dict[str, str]:
    """Render a datetime as a relative string + absolute UTC tooltip.

    Returns `{"rel": "4h 12m ago" | "in 7m" | "—", "abs": "2026-… UTC"}`.
    Templates show `rel` with `abs` in a `title=` tooltip.
    """
    if dt is None:
        return {"rel": "—", "abs": ""}
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    delta = dt - now
    future = delta.total_seconds() >= 0
    secs = int(abs(delta.total_seconds()))
    if secs < 60:
        chunk = f"{secs}s"
    elif secs < 3600:
        chunk = f"{secs // 60}m"
    elif secs < 86400:
        chunk = f"{secs // 3600}h {(secs % 3600) // 60}m"
    else:
        chunk = f"{secs // 86400}d {(secs % 86400) // 3600}h"
    rel = f"in {chunk}" if future else f"{chunk} ago"
    return {"rel": rel, "abs": dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")}


# The APScheduler jobstore table (`apscheduler_jobs`) is created by
# SQLAlchemyJobStore, not by our `Base.metadata` — it may not exist (scheduler
# never started, or a fresh test DB). Check existence with the inspector first
# (non-destructive) so a missing table is a clean None rather than a poisoned
# transaction that would break the queries that run after it.
def _read_jobstore(session: Session) -> dict[str, float] | None:
    """Return {job_id: next_run_time_epoch}, or None if the table is absent."""
    from sqlalchemy import inspect, text

    bind = session.get_bind()
    if not inspect(bind).has_table("apscheduler_jobs"):
        return None
    result = session.execute(text("SELECT id, next_run_time FROM apscheduler_jobs")).all()
    return {str(r[0]): r[1] for r in result if r[1] is not None}


def _scheduler_section(session: Session, now: datetime) -> list[dict[str, Any]]:
    jobstore = _read_jobstore(session)
    out: list[dict[str, Any]] = []
    for job_id in EXPECTED_JOB_IDS:
        if jobstore is None or job_id not in jobstore:
            out.append({"job_id": job_id, "when": {"rel": "—", "abs": ""}, "status": "disabled"})
            continue
        next_dt = datetime.fromtimestamp(jobstore[job_id], tz=UTC)
        stale = next_dt < now - _STALE_GRACE
        out.append(
            {
                "job_id": job_id,
                "when": _fmt_rel(next_dt, now=now),
                "status": "stale" if stale else "scheduled",
            }
        )
    return out


def _last_ingest_by_kind(session: Session) -> dict[str, datetime]:
    """Most-recent successful ingest-batch `started_at` per source kind.

    Source-health rows show "last ingest" derived from the batch job's
    scheduler_runs, since per-source timestamps aren't tracked separately for
    every source. job_id format is "<kind>.ingest_batch".
    """
    rows = session.execute(
        select(SchedulerRun.job_id, func.max(SchedulerRun.started_at))
        .where(SchedulerRun.ok.is_(True), SchedulerRun.job_id.like("%.ingest_batch"))
        .group_by(SchedulerRun.job_id)
    ).all()
    out: dict[str, datetime] = {}
    for job_id, started in rows:
        kind = job_id.split(".", 1)[0]
        if started is not None:
            out[kind] = started
    return out


def _sources_section(session: Session, now: datetime) -> list[dict[str, Any]]:
    last_ingest = _last_ingest_by_kind(session)
    sources = session.execute(select(Source).order_by(Source.kind, Source.name)).scalars().all()
    by_kind: dict[str, dict[str, Any]] = {}
    for s in sources:
        grp = by_kind.setdefault(s.kind, {"kind": s.kind, "active": 0, "disabled": 0, "rows": []})
        if s.is_active:
            grp["active"] += 1
        else:
            grp["disabled"] += 1
        if s.consecutive_failures >= 3 or not s.is_active:
            mark = "fail"
        elif s.consecutive_failures >= 1:
            mark = "warn"
        else:
            mark = "ok"
        grp["rows"].append(
            {
                "name": s.name,
                "consecutive_failures": s.consecutive_failures,
                "last_ingest": _fmt_rel(last_ingest.get(s.kind), now=now),
                "mark": mark,
            }
        )
    return list(by_kind.values())


def _cost_by_task(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(
        select(
            LLMRun.task,
            func.count().label("calls"),
            func.avg(LLMRun.est_cost_usd).label("avg_cost"),
            func.sum(LLMRun.est_cost_usd).label("total_cost"),
        )
        .group_by(LLMRun.task)
        .order_by(func.sum(LLMRun.est_cost_usd).desc())
    ).all()
    return [
        {
            "task": r.task,
            "calls": r.calls,
            "avg_cost": float(r.avg_cost or 0.0),
            "total_cost": float(r.total_cost or 0.0),
        }
        for r in rows
    ]


def _cost_by_day(session: Session, now: datetime) -> list[dict[str, Any]]:
    since = now - timedelta(days=7)
    rows = session.execute(
        select(LLMRun.created_at, LLMRun.est_cost_usd).where(LLMRun.created_at >= since)
    ).all()
    buckets: dict[str, dict[str, float]] = {}
    for created_at, cost in rows:
        if created_at is None:
            continue
        day = (
            created_at.astimezone(UTC).date().isoformat()
            if created_at.tzinfo
            else created_at.date().isoformat()
        )
        b = buckets.setdefault(day, {"calls": 0.0, "cost": 0.0})
        b["calls"] += 1
        b["cost"] += float(cost or 0.0)
    return [
        {"date": day, "calls": int(b["calls"]), "cost": b["cost"]}
        for day, b in sorted(buckets.items(), reverse=True)
    ]


def _collect(session: Session) -> dict[str, Any]:
    """Build the full dashboard context. One pass; cheap aggregate queries."""
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)
    day_ago = now - timedelta(hours=24)

    pending = session.execute(
        select(func.count()).select_from(Candidate).where(Candidate.decision == Decision.PENDING)
    ).scalar_one()
    today_cost = session.execute(
        select(func.coalesce(func.sum(LLMRun.est_cost_usd), 0.0)).where(
            LLMRun.created_at >= today_start
        )
    ).scalar_one()
    week_cost = session.execute(
        select(func.coalesce(func.sum(LLMRun.est_cost_usd), 0.0)).where(
            LLMRun.created_at >= week_start
        )
    ).scalar_one()
    raw_count = session.execute(select(func.count()).select_from(RawSignal)).scalar_one()
    signal_text_count = session.execute(select(func.count()).select_from(SignalText)).scalar_one()
    total_cost = session.execute(
        select(func.coalesce(func.sum(LLMRun.est_cost_usd), 0.0))
    ).scalar_one()
    total_candidates = session.execute(select(func.count()).select_from(Candidate)).scalar_one()

    active_by_kind = session.execute(
        select(Source.kind, func.count())
        .where(Source.is_active.is_(True))
        .group_by(Source.kind)
        .order_by(Source.kind)
    ).all()

    # Cache hit ratio: read / (read + write). 0 when no cache tokens recorded.
    cache_read, cache_write = session.execute(
        select(
            func.coalesce(func.sum(LLMRun.cache_read_tokens), 0),
            func.coalesce(func.sum(LLMRun.cache_write_tokens), 0),
        )
    ).one()
    cache_total = (cache_read or 0) + (cache_write or 0)
    cache_hit_ratio = (cache_read / cache_total) if cache_total else 0.0

    recent_runs = (
        session.execute(select(SchedulerRun).order_by(SchedulerRun.started_at.desc()).limit(20))
        .scalars()
        .all()
    )

    sched_errors = (
        session.execute(
            select(SchedulerRun)
            .where(SchedulerRun.ok.is_(False), SchedulerRun.started_at >= day_ago)
            .order_by(SchedulerRun.started_at.desc())
        )
        .scalars()
        .all()
    )
    llm_errors = (
        session.execute(
            select(LLMRun)
            .where(LLMRun.ok.is_(False), LLMRun.created_at >= day_ago)
            .order_by(LLMRun.created_at.desc())
        )
        .scalars()
        .all()
    )

    now_for_rel = now
    return {
        "active": "ops",
        "generated": _fmt_rel(now, now=now),
        "cards": {
            "pending": pending,
            "today_cost": float(today_cost),
            "week_cost": float(week_cost),
            "unprocessed": max(raw_count - signal_text_count, 0),
            "active_sources": [{"kind": k, "n": n} for k, n in active_by_kind],
            "cost_per_candidate": (float(total_cost) / total_candidates)
            if total_candidates
            else 0.0,
        },
        "jobs": _scheduler_section(session, now_for_rel),
        "recent_runs": [
            {
                "started": _fmt_rel(r.started_at, now=now_for_rel),
                "job_id": r.job_id,
                "ok": r.ok,
                # Key name avoids the dict-method collision: a key called
                # "items" would render as `<built-in method items of dict>`
                # because Jinja2 resolves `r.items` via getattr first.
                "items_processed": r.items_processed,
                "error": r.error,
            }
            for r in recent_runs
        ],
        "sources": _sources_section(session, now_for_rel),
        "cost_by_task": _cost_by_task(session),
        "cost_by_day": _cost_by_day(session, now_for_rel),
        "cache_hit_ratio": cache_hit_ratio,
        "sched_errors": [
            {
                "time": _fmt_rel(r.started_at, now=now_for_rel),
                "job_id": r.job_id,
                "error": r.error or "",
            }
            for r in sched_errors
        ],
        "llm_errors": [
            {
                "time": _fmt_rel(r.created_at, now=now_for_rel),
                "task": r.task,
                "attempts": r.attempts,
                "error": r.error or "",
            }
            for r in llm_errors
        ],
    }


@router.get("/ops", response_class=HTMLResponse)
def ops(request: Request, session: Annotated[Session, Depends(_session)]) -> HTMLResponse:
    """Full dashboard page (chrome + body)."""
    return templates.TemplateResponse(request, "ops.html", _safe_context(session))


@router.get("/ops/body", response_class=HTMLResponse)
def ops_body(request: Request, session: Annotated[Session, Depends(_session)]) -> HTMLResponse:
    """Just the data area — polled by HTMX every 30s."""
    return templates.TemplateResponse(request, "_ops_body.html", _safe_context(session))


@router.post("/ops/scheduler/restart", response_class=HTMLResponse)
def restart_scheduler(
    request: Request, session: Annotated[Session, Depends(_session)]
) -> HTMLResponse:
    """Tear down + restart the APScheduler instance in place.

    Operator-initiated remedy when /ops surfaces a STALE job (the
    cluster-before-normalize symptom from earlier today, or anything that
    wedges the scheduler without taking down uvicorn). uvicorn keeps running;
    in-flight HTTP handlers are unaffected. Job definitions persist in the
    SQLAlchemyJobStore and are re-read on restart.

    Always writes a `scheduler_runs` row with `job_id="ops.manual_restart"`
    as the audit trail — surfaces in /ops Recent runs immediately. Per
    orchestrator request 025; see also CLAUDE.md → /ops mutation pattern
    (explicit, logged, idempotent, minimal-scope).
    """
    scheduler = getattr(request.app.state, "scheduler", None)
    started_at = datetime.now(UTC)
    error_msg: str | None = None

    if scheduler is None:
        error_msg = "scheduler not initialized on app.state — restart aborted"
        logger.error("ops.manual_restart: %s", error_msg)
    else:
        try:
            # `wait=False` so we don't block on in-flight worker threads.
            # If the scheduler is already stopped (e.g., a prior shutdown left
            # it that way), this raises SchedulerNotRunningError — caught
            # below and treated as "proceed to start", which is what the
            # operator wanted anyway.
            try:
                scheduler.shutdown(wait=False)
            except Exception as shutdown_exc:  # noqa: BLE001 — APScheduler can raise multiple shapes
                logger.info(
                    "ops.manual_restart shutdown (likely already stopped): %s",
                    shutdown_exc,
                )
            scheduler.start()
        except Exception as exc:  # noqa: BLE001 — record + surface, don't 500
            error_msg = f"{type(exc).__name__}: {exc}"
            logger.exception("ops.manual_restart failed")

    finished_at = datetime.now(UTC)
    session.add(
        SchedulerRun(
            job_id="ops.manual_restart",
            started_at=started_at,
            finished_at=finished_at,
            ok=(error_msg is None),
            error=error_msg,
            items_processed=None,
        )
    )
    session.commit()

    # Return the refreshed body — same partial the 30s auto-refresh uses,
    # so the operator immediately sees the new next_run_times and the
    # ops.manual_restart row in Recent runs.
    return templates.TemplateResponse(request, "_ops_body.html", _safe_context(session))


def _safe_context(session: Session) -> dict[str, Any]:
    """Collect dashboard data, degrading to a busy-state flag on a locked DB.

    SQLite read locks can briefly block under concurrent writes; render a
    "temporarily busy" placeholder rather than 500-ing the operator's page.
    """
    try:
        ctx = _collect(session)
        ctx["db_busy"] = False
        return ctx
    except OperationalError:
        return {"active": "ops", "db_busy": True}
