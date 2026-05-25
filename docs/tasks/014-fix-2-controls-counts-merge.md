# 014-fix-2 — scheduler controls + nav counts + candidate merge

**Goal:** three operator-experience improvements surfaced during active triage,
bundled into one PR (orchestrator request 031): (1) pause/resume the scheduler
from `/ops`, (2) live counts on the inbox nav links, (3) merge duplicate
candidates into one via Opus.

**Complexity:** M

Depends on: 014-fix-1 (inbox detail view), 024 (`/ops` dashboard), 025
(buildability — the merge re-assesses it).

## Feature 1 — scheduler pause/resume

- `POST /ops/scheduler/pause` + `/ops/scheduler/resume` (HTMX, mirror the
  existing `/ops/scheduler/restart`). Global `pause()`/`resume()` — NOT
  `shutdown()`; job state is preserved, only scheduled firings stop. Triage
  actions, manual runs, and LLM calls keep working while paused.
- `/ops` shows a status pill (green=running / yellow=paused / red=stopped) and
  conditionally a **stop** or **resume** button (+ the existing restart).
- Both endpoints log a `scheduler_runs` audit row (`ops.manual_pause` /
  `ops.manual_resume`).
- **Pause survives container restarts.** APScheduler's `pause()` is in-memory
  only, so the intent is persisted in a new `runtime_state` key/value table; the
  lifespan re-applies `pause()` on startup if the flag is set
  (`apfun/scheduler/pause_state.py`). Per request 031 §1 (verified pause is not
  jobstore-persisted; added the minimal table the spec calls for).

## Feature 2 — nav counts

- The inbox nav shows `pending (N) · approved (N) · rejected (N) · unsure (N) ·
  ☐ hide non-software (N)`. Extracted into a shared `_inbox_nav.html` partial so
  the listing AND the detail view carry the same chrome.
- The four decision counts are absolute (soft-deleted excluded). The
  hide-non-software `(N)` = how many candidates the filter would remove from the
  **current** view (`current_decision` + `buildability='non_software'`) — "matches
  the criteria," consistent regardless of toggle state.
- Computed per page load (`_nav_counts`); 5 COUNTs, negligible at current scale.
  No caching until measured >50ms.

## Feature 3 — candidate merge

- New nullable self-FK `candidates.merged_into_id` (migration `b2c4d6e8f0a1`,
  FK-safe + data-preservation-tested). NULL = live; non-null = soft-deleted into
  the referenced candidate.
- **Soft-deletion:** every listing filters `merged_into_id IS NULL`; the detail
  view of a merged candidate 303-redirects to `/inbox/<parent>?merged_from=<id>`
  (the parent renders a "merged from #N" banner).
- **Selection UI:** a checkbox per card; one `<form action="/inbox/merge">` wraps
  the listing; the submit button enables at 2+ selections (small delegated JS)
  and `onsubmit` confirms. Decision buttons are `type="button"` so they don't
  submit the merge form.
- **Merge logic** (`apfun/pipeline/merge.py`): validate (≥2 distinct, all exist,
  none already-merged) → Opus synthesis via `merge_candidates.j2` (`MergedCard`:
  problem_statement, suspected_user, seed_keywords, buildability + rationale) →
  single transaction: insert the new candidate (decision=`pending`), re-link the
  **distinct** contributing signals (dedup — `candidate_signals` has a composite
  PK; a shared signal isn't double-counted), soft-delete the sources. Weight is
  the SUM over the distinct signals (computed in-app, the existing derived way).
- The new candidate is always `pending` (merging demands fresh review). A merged
  source keeps its own decision on the soft-deleted row (a rejected source stays
  rejected — no silent flip). The `merged_into_id` chain + `created_at` is the
  audit trail; no `'merged'` approval value added (the chain suffices, per the
  spec's primary statement — avoids a third enum/CHECK migration).
- The Opus call runs **inline** in the `POST /inbox/merge` handler. The handler
  is `def` (sync) → runs in Starlette's threadpool, so it doesn't block the event
  loop; an operator-initiated, synchronous-by-design action (the operator waits
  for the merged result), in the same spirit as the /ops restart button. `merge`
  is registered in `JUDGMENT_TASKS` + `DEFAULT_EFFORT` (medium). ~$0.013/merge.

## Out of scope
Per-job pause; nav-count caching; auto-detect-similar; unmerge; bulk
approve/reject; drag-and-drop. (Request 031 "What's NOT in scope".)

## Tests
- Migration data-preservation (`test_migration_merged_into_fk_safety.py`).
- Merge logic (`test_merge.py`): validation, single-transaction persist, signal
  re-link + shared-signal dedupe, soft-delete, rejected-stays-rejected, weight.
- Pause/resume (`test_ops_scheduler_pause.py`): endpoints, audit rows,
  runtime_state flag, status indicator, failure recorded not 500'd.
- Nav counts + soft-delete + detail redirect + merge endpoint
  (`test_inbox_merge_and_counts.py`).

## Schema migrations (FK-safe; snapshot before applying)
1. `b2c4d6e8f0a1` — `candidates.merged_into_id` (batch recreate, data-preservation-tested).
2. `c3d5e7f9a1b2` — `runtime_state` table (new table, no children).

## Post-merge operator checks (request 031 §"Empirical validation")
1. Pause → confirm no ingest fires for ~10-15 min → resume → confirm jobs resume.
2. Navigate inbox views; counts match what's shown.
3. Merge 2-3 actual duplicates; eyeball the merged problem_statement. If quality
   is poor, open a follow-up to tune `merge_candidates.j2`.
