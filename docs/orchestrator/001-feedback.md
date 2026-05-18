# Feedback 001 — Gate 2 architecture review

**Date:** 2026-05-17
**Request:** 001-gate2-architecture-review.md
**Outcome:** Approved with four required changes before task 001.

## Required changes

### 1. Sync DB layer, not async

SQLite under `aiosqlite` serializes writes through a single connection. With APScheduler firing concurrent jobs that all write to SQLite, you'll hit `database is locked` errors. Async + SQLite + multiple writers is a known anti-pattern.

For v1: sync SQLAlchemy 2.x + stdlib `sqlite3`. FastAPI routes can stay async-capable but use sync sessions for DB work. Revisit only if/when we migrate to Postgres.

### 2. Split `candidates.status` into two columns

Mixing HITL lifecycle (`pending_demand`, `pending_review`, `approved`, `rejected`) with execution state (`competitive`, `scoring`, `synthesizing`, `done`, `synthesis_failed`) in one column has two problems:

- Inbox queries get awkward (multi-value `IN` clauses)
- Recovery from partial pipeline runs is harder — the distinction between human decision and machine progress is lost

Use two columns:

- `decision` enum: `pending` / `approved` / `rejected` / `auto_killed`
- `pipeline_stage` enum: `none` / `competitive` / `scoring` / `synthesizing` / `done` / `failed`

### 3. Resend for digest email, not Postmark

At v1 scale (one digest a week to one address — the operator himself), Postmark is overkill. Resend's free tier (~3000/mo) covers a weekly self-email indefinitely. Clean API.

### 4. Add Complexity tag to each task file

Format: `Complexity: S/M/L` line per task.

- S ≈ 1hr
- M ≈ half-day
- L ≈ full day

Pure planning aid for picking what to tackle in a given session.

## What was good (preserve)

These were Claude Code's additions/choices beyond the brief that are worth keeping:

- `llm_runs` table for per-call cost tracking (not in the brief; correct instinct)
- `api_usage` table generalized beyond DataForSEO (extensible to other paid providers)
- `model_version` column on `scores` (so scoring formula can evolve without losing history's interpretability)
- Tailwind via standalone CLI binary, not Node (keeps the container Node-only-for-Claude-Code)
- Status enum on `candidates` covering full lifecycle including `auto_killed` and `budget_blocked` (real failure modes, not just happy path)

## Next step

After applying these four changes, proceed to task 001 (scaffold FastAPI app).
