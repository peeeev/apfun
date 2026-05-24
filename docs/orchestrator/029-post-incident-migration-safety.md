# Request 029: post-incident — migration safety, backup discipline, dev-runtime workflow

**Date:** 2026-05-24

**Context.** A migration I ran caused real production data loss. Recovered (partially), fixed, and shipped a guard — but the incident surfaces three convention-level questions worth your direction, including two that materially touch `CLAUDE.md`.

## The incident

Applying migration `7f3a9c2e1d04` (the task 014-fix-1 / request 028 batch CHECK-constraint rewrite to add the `unsure` decision value) to the **live** DB cascade-deleted child tables:

- `candidate_signals` → 0 rows (all signal↔candidate links)
- `approvals` → 0 rows (all decision audit + notes)
- `llm_runs.candidate_id` → nulled

Root cause: Alembic `render_as_batch` recreates the table (CREATE-new → copy → **DROP-old** → rename); with `foreign_keys=ON` (which `apfun.db`'s connect listener sets, and `migrations/env.py` imported that engine), SQLite's implicit DELETE-before-DROP fired every `ON DELETE CASCADE` pointing at `candidates`. There was **no backup**.

**Why my pre-merge validation missed it:** I "validated on a throwaway DB," but the throwaway was *empty* — no child rows to cascade-delete. I tested the constraint change, not data preservation.

## What I just did (PR #27, merged)

- `migrations/env.py` now runs migrations through a dedicated engine with `PRAGMA foreign_keys=OFF` (set at connect time — the only point SQLite honors it; doing it via `connection.execute` post-connect is a no-op inside Alembic's transaction and broke commit handling on a first attempt). App engine keeps FK ON.
- `tests/integration/test_migration_fk_safety.py` — seeds parent + children, runs the batch migration, asserts children survive (the validation I should have done).
- Also fixed a separate post-merge breakage: `/ops/scheduler/restart` 500'd because PR #23's `_safe_context(session, started_at)` signature wasn't reconciled with PR #24's call site (5 tests were red on `main`).
- CLAUDE.md Lessons Learned added for the cascade trap + the seed-child-rows validation discipline.

## Recovery (done)

`raw_signals` (582) + `signal_text` (582) were intact — the source data survived; candidates are derived. Recovered by: delete the 158 orphaned candidates, re-cluster the intact signals → **168 candidates rebuilt with proper links**. Permanently lost: the 7 approved / 11 rejected decisions + all approval notes/audit (no backup). Operator re-triages.

## What I would do next without intervention

Keep operating; adopt the dev-runtime workflow below; treat the new conventions as in force.

## Specific questions / direction wanted

1. **Migration data-preservation convention.** Bless / refine: *every migration that touches a table with `ON DELETE CASCADE` (or `SET NULL`) children must be data-preservation-tested — seed child rows, migrate, assert survival — not just schema-tested.* Should this be a hard CLAUDE.md convention? Worth a CI gate (the task 023 CI, when live, could run the integration migration test)?

2. **Backup discipline (the unrecoverable part).** The incident was *recoverable in principle* but *unrecoverable in practice* because there was no backup. Proposal: a pre-migration snapshot — e.g., a `scripts/db_snapshot.sh` (`cp data/apfun.db data/backups/apfun-<rev>-<ts>.db`) run before any `alembic upgrade`, and/or a documented operator step. Do you want this as a convention + helper? Retention policy?

3. **Dev-runtime workflow (materially affects how Claude Code operates).** The operator switched the container CMD to `uvicorn … --reload --reload-dir /workspace/apfun`. Because `/workspace` is a single shared dev+prod checkout, my editing/branch-switching there churns the *live* service (reloads + scheduler bounces) and can transiently run unmerged branch code against prod. Adopted workaround (validated): **`/workspace` stays permanently on `main`; I do all branch work in `git worktree`s outside the watched path** (`/tmp/apfun-<task>`, own `uv` venv → tests run against branch code); operator deploys via `git pull` on `main`. Should this be formalized in CLAUDE.md (e.g., a "Dev runtime / worktree workflow" section), or would you rather drop `--reload` and use deliberate restart-on-deploy instead?

4. **Lost decisions/audit.** Accept (re-triage from scratch), or any follow-up you want (e.g., the operator was only ~half a day in, so the loss is small)?

## Relevant files

- PR #27 (merged): `migrations/env.py`, `tests/integration/test_migration_fk_safety.py`, `apfun/web/routes/ops.py`, `CLAUDE.md`
- The migration: `migrations/versions/7f3a9c2e1d04_add_unsure_decision_value.py`
- Live state: 168 candidates (all pending) rebuilt; `raw_signals`/`signal_text` intact; null rate ~71% (runbook 004 still pending).
