# 021 — Admin UI: sources health + projects

**Goal:** Two small operational views.

**Complexity:** S

Depends on: 012, 013, 020.

## Deliverables
- `GET /sources` (`apfun/web/routes/sources.py`): table of `sources` with last-run timestamp, ok/fail of the most recent `scheduler_runs` row, items processed last run, button to "run now" (one-shot APScheduler trigger).
- `GET /projects` (`apfun/web/routes/projects.py`): list of `projects` linked to `opportunities`. Each row shows slug, subdomain, status, age. Button to mark `placeholder → in_dev → live → sunset` (simple state machine; no validation).
- `POST /projects/from-opportunity/{opportunity_id}` — creates a placeholder project row with a slug derived from the opportunity. Does NOT call `new-project.sh` on the host (that's a manual step the human runs; the brief is explicit that subdomain scaffolding happens via host script). Template makes the host-side command copy-pasteable.

## Acceptance
- Sources view auto-refreshes via HTMX polling every 30s; ok/fail badges turn red when the last run failed.
- "Run now" enqueues the job and shows a transient running indicator.
- Creating a project from an opportunity writes the row and shows the `new-project.sh <slug>` snippet to copy.

## Notes
- Don't invoke `new-project.sh` from inside the container — it lives on the host outside `/workspace`. The UI surfaces the command for the human to run.
- **LLM budget health panel** (per orchestrator feedback 006 — the feedback referenced task 022, but the operational sources-health view lives here in 021, so the panel belongs alongside it). Small read-only panel under sources health that aggregates `llm_runs`: rows-per-task counts (trigger at 50 for any single task), count of WARNING-level "thinking budget" log entries logged or — better — a `budget_warned_at` timestamp column added when wiring this view, and total count of `task='synthesize'` rows (trigger at 10). These are *eventual* retune signals, not real-time alerts — the 90%-of-budget warning already fires in logs in real time as the highest-signal trigger. When a trigger fires, open an orchestrator request with the aggregates; don't tune `DEFAULT_THINKING_BUDGET` silently.
