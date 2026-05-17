# 012 — Scheduler

**Goal:** APScheduler `BackgroundScheduler` running inside the FastAPI process, with a SQLite jobstore so restarts don't lose jobs, registering all Stage 1 + Stage 2 jobs.

**Complexity:** M

Depends on: 005, 006, 007, 008, 009, 010, 011 (any that exist; jobs for missing sources can be left out and added later).

## Deliverables
- Dep: `apscheduler`.
- `apfun/scheduler/setup.py` initializes `BackgroundScheduler` with `SQLAlchemyJobStore(url=settings.db_url, tablename="apscheduler_jobs")` and a `ThreadPoolExecutor` (default pool size 10).
- Started from a FastAPI lifespan handler in `apfun/main.py`; stopped on shutdown.
- `apfun/scheduler/jobs.py` registers:
  - Reddit ingest — every 6 hours
  - HN ingest — every 6 hours, offset by 1h
  - ProductHunt — daily at 07:00 UTC
  - IndieHackers — daily at 09:00 UTC
  - Review miner per-product — weekly Mon 03:00 UTC
  - Stage 1 clustering — every 2 hours
  - Stage 2 demand check — daily 06:00 UTC
- Every job is a sync function wrapped to write a `scheduler_runs` row (started_at, finished_at, ok, items_processed, error).
- Jobs are idempotent: re-running mid-window produces no duplicates and no errors.
- Single-process: APScheduler's built-in `coalesce=True` + `max_instances=1` per job ID prevents overlap.

## Acceptance
- App starts, scheduler registers all jobs, `/healthz` includes `scheduler.running == true`.
- Killing the app mid-job and restarting: no duplicate runs, the next firing succeeds.
- Unit test for the wrapper: a job that raises still produces a `scheduler_runs` row with `ok=false` and the error message.

## Notes
- `BackgroundScheduler` runs each job in a worker thread; no event-loop interaction. Jobs use sync clients (`httpx.Client`, `anthropic.Anthropic`) and sync DB sessions directly. Do **not** import `AsyncIOScheduler`, `AsyncAnthropic`, or `httpx.AsyncClient` inside jobs — that's the locking footgun this stack exists to avoid.
- SQLite serializes writes; `busy_timeout=5000` from task 002 gives jobs a 5-second window before `OperationalError`. If a single job exceeds 15 minutes wall time, split it — long-running threads delay graceful shutdown.
