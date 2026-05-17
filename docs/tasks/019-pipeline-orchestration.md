# 019 — Pipeline orchestration

**Goal:** When a candidate is approved in the inbox, fire Stage 3 → 4 → 5 in order on a `BackgroundScheduler` worker thread, with progress visible via `candidate.pipeline_stage`.

**Complexity:** M

Depends on: 014, 016, 017, 018.

## Deliverables
- `apfun/pipeline/run.py`:
  - `def run_pipeline(candidate_id, force=False)`: orchestrates competitive scrape → review mining for top-3 competitors → scoring → synthesis. Each phase writes its own rows; orchestrator updates `candidate.pipeline_stage` along the way (`none` → `competitive` → `scoring` → `synthesizing` → `done` | `failed`). `decision` is not touched (it stays `'approved'`).
  - Idempotent at each phase: re-running skips already-complete phases unless `force=True`.
  - Each phase wraps in try/except, writes a `pipeline_runs` row, and re-raises so the scheduler-runs wrapper logs it too.
- New table `pipeline_runs(id, candidate_id FK, phase, started_at, finished_at, ok, error, reason, payload_json)` added via a small Alembic revision in this task. `phase` ∈ same values as `pipeline_stage` minus `none`. `reason` is a short tag for the failure mode (e.g. `budget_blocked`, `synthesis_invalid_json`, `scrape_blocked`).
- Triggered from task 014 via `scheduler.add_job(run_pipeline, "date", run_date=now+1s, args=[candidate_id], id=f"pipeline-{candidate_id}", replace_existing=True)`. Distinct job IDs per candidate so parallel pipelines work, but the same candidate can't double-run.
- Budget-blocked candidates: when Stage 3 raises `BudgetExceeded`, the orchestrator sets `pipeline_stage='failed'` and writes `pipeline_runs(phase='competitive', ok=False, reason='budget_blocked')`. A monthly retry job (registered in task 012) re-queues candidates whose `pipeline_stage='failed'` AND latest `pipeline_runs.reason='budget_blocked'`.

## Acceptance
- Approving a candidate from the inbox results in a completed pipeline run within minutes; the candidate ends in `pipeline_stage='done'` with rows in `competitive_analyses`, `scores`, and `opportunities`.
- Killing the app mid-pipeline and restarting picks up from the next un-completed phase.
- `run_pipeline(candidate_id, force=True)` reruns from the beginning.

## Notes
- Don't introduce Celery or RQ here. A one-shot `BackgroundScheduler` job is the right size.
- DB-layer concurrency: each phase opens its own short sync session (don't hold one across the whole pipeline). SQLite `busy_timeout` from task 002 handles transient contention with other scheduler jobs.
