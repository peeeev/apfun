# 012 — Scheduler

**Goal:** APScheduler running inside the FastAPI process, with a SQLite jobstore so restarts don't lose jobs, registering all Stage 1 + Stage 2 jobs.

Depends on: 005, 006, 007, 008, 009, 010, 011 (any that exist; jobs for missing sources can be left out and added later).

## Deliverables
- Dep: `apscheduler`.
- `apfun/scheduler/setup.py` initializes `AsyncIOScheduler` with `SQLAlchemyJobStore(url=settings.db_url, tablename="apscheduler_jobs")`.
- Started from a FastAPI lifespan handler in `apfun/main.py`; stopped on shutdown.
- `apfun/scheduler/jobs.py` registers:
  - Reddit ingest — every 6 hours
  - HN ingest — every 6 hours, offset by 1h
  - ProductHunt — daily at 07:00 UTC
  - IndieHackers — daily at 09:00 UTC
  - Review miner per-product — weekly Mon 03:00 UTC
  - Stage 1 clustering — every 2 hours
  - Stage 2 demand check — daily 06:00 UTC
- Every job wraps its body in a `scheduler_runs` row (started_at, finished_at, ok, items_processed, error).
- Jobs are idempotent: re-running mid-window produces no duplicates and no errors.
- Distributed-lock not needed (single process). Use APScheduler's built-in `coalesce=True` + `max_instances=1` per job.

## Acceptance
- App starts, scheduler registers all jobs, `/healthz` includes `scheduler.running == true`.
- Killing the app mid-job and restarting: no duplicate runs, the next firing succeeds.
- Unit test for the wrapper: a job that raises still produces a `scheduler_runs` row with `ok=false` and the error message.

## Notes
- AsyncIOScheduler runs jobs in the FastAPI event loop. Sync libraries (pytrends, playwright in sync mode) must use `asyncio.to_thread`.
- If a single job ever exceeds 15 minutes wall time, split it. APScheduler holds the event loop while a job runs in-process.
