# 019 — Pipeline orchestration

**Goal:** When a candidate is approved in the inbox, fire Stage 3 → 4 → 5 in order, async, with status visible.

Depends on: 014, 016, 017, 018.

## Deliverables
- `apfun/pipeline/run.py`:
  - `async def run_pipeline(candidate_id)`: orchestrates competitive scrape → review mining for top-3 competitors → scoring → synthesis. Each phase writes its own rows; orchestrator updates `candidate.status` along the way (`approved` → `competitive` → `scoring` → `synthesizing` → `done` or `failed`).
  - Idempotent at each phase: re-running skips already-complete phases unless `--force` is passed.
  - Each phase wraps in try/except, writes a `pipeline_runs` row (new table — add via a small Alembic revision in this task), and re-raises so the scheduler logs it too.
- Triggered from task 014's inbox by enqueueing onto the APScheduler scheduler with a one-shot `DateTrigger(now + 1s)`. Don't block the HTTP handler.
- Budget-blocked candidates (DataForSEO cap hit) are marked `status=budget_blocked`; a daily job retries them at month rollover.

## Acceptance
- Approving a candidate from the inbox results in a completed pipeline run within minutes; the candidate ends in `status=done` with rows in `competitive_analyses`, `scores`, and `opportunities`.
- Killing the app mid-pipeline and restarting picks up from the next un-completed phase.
- Force-rerun works.

## Notes
- Don't introduce Celery or RQ here. A one-shot APScheduler job is the right size.
- If pipelines start to overlap (multiple approvals in quick succession), APScheduler's per-job `max_instances=1` is too restrictive; use a per-job-id `max_instances` so distinct candidates run in parallel but the same candidate doesn't double-run.
