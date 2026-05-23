# 024 — `/ops` operator dashboard

**Goal:** a single read-only web page at `apfun.online/ops` showing funnel health at a glance — so diagnostics don't require SSH-ing in to run ad-hoc `sqlite3` queries.

**Complexity:** M

Depends on: 012 (scheduler / `scheduler_runs`), 013/014 (web chrome). Per orchestrator request 023.

## Deliverables

- `apfun/web/routes/ops.py` — `GET /ops` (full page) + `GET /ops/body` (HTMX 30s-refresh partial), sharing one `_collect()` data builder. Read-only; no mutations; no LLM calls.
- `apfun/web/templates/ops.html` + `_ops_body.html` — six sections (KPI cards, scheduler calendar w/ STALE detection, recent runs, source health, LLM cost breakdown, recent errors).
- Nav link to `/ops` in `_base.html`; `/ops` utility classes in `app.css`.
- `apfun/scheduler/jobs.py` — `EXPECTED_JOB_IDS` constant (single source of truth for the dashboard's "disabled" diff; a test keeps it in lockstep with `register_all`).
- Behind existing Apache htpasswd; no app-level auth.

## Acceptance

- `/ops` renders all six sections; `/ops/body` is a chrome-less fragment with `hx-trigger="every 30s"`.
- A job with `next_run_time` in the past by >5 min shows `⚠ STALE`; a future one shows `✓ scheduled`; a job absent from the jobstore shows `⏸ disabled`.
- Error sections empty-state ("No errors in last 24h ✓") when clean; surface failed `scheduler_runs`/`llm_runs` within 24h otherwise.
- A locked DB renders a "temporarily busy" placeholder, not a 500.

## Notes

- All data from existing tables — no schema changes. The `apscheduler_jobs` table (created by APScheduler's jobstore, not `Base.metadata`) is read via an existence-checked raw SELECT so a missing table degrades to "all disabled" rather than erroring.
- Desktop-oriented (operator tool, not a customer surface); tables don't reflow for mobile. Per request 023 Q5.
- Out of scope: mutations, drill-down routes, charts, time-window pickers. Per request 023.
