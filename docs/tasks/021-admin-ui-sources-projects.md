# 021 — Admin UI: sources health + projects

**Goal:** Two small operational views.

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
