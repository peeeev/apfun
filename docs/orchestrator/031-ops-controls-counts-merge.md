# Request 031: task 014-fix-2 — scheduler controls + nav counts + candidate merge

**Date:** 2026-05-24
**Context:** Operator triaged a substantial chunk of the inbox and surfaced three friction points: (1) need to pause incoming stream to focus on triage without new candidates arriving; (2) want counts on nav links to know queue sizes at a glance; (3) some candidates are obviously the same and should be merged into one with combined weight.

All three are operator-experience improvements from active inbox use. None of them are blocking, but together they materially improve the triage workflow.

## Goal

1. Operator can pause and resume the scheduler from `/ops` without losing job state or restarting the container.
2. Inbox nav links show counts: `pending (N)`, `approved (N)`, `rejected (N)`, `unsure (N)`, `hide non-software (N)`.
3. Operator can select multiple candidates via checkboxes, merge them into one via Opus, with all contributing signals re-linked and weight recomputed.

## Scope

### Feature 1 — Scheduler Stop/Start

- Add `/ops/scheduler/pause` and `/ops/scheduler/resume` endpoints (HTMX-driven, mirror the existing `/ops/scheduler/restart` from request 025).
- Use APScheduler's `pause()` and `resume()` methods (not `shutdown()` — pause is global, preserves all job state, allows existing operator-initiated work to continue).
- The `/ops` page renders conditionally:
  - When scheduler is running: show **Stop scheduler** button + status indicator (green dot / "running")
  - When scheduler is paused: show **Resume scheduler** button + status indicator (yellow dot / "paused")
- Both endpoints log to `scheduler_runs` with `job_id="ops.manual_pause"` or `"ops.manual_resume"`.
- Status displayed alongside the existing `restart` button (probably the same section in /ops, with three buttons total in some logical layout).

**Persistence question:** verify APScheduler's pause state survives container restarts via the SQLAlchemyJobStore. If it does (likely), no extra work. If it doesn't, persist the pause state in a small config table (e.g., `runtime_state(key TEXT PRIMARY KEY, value TEXT)`) and check it on lifespan startup, applying pause if needed. Don't over-engineer this; check first, add minimal persistence only if needed.

**What still works while paused:**
- FastAPI / inbox / /ops continue serving
- Operator-initiated cluster runs (via REPL or manual buttons) still work
- Triage actions (approve/reject/unsure/merge/notes) still work
- LLM calls from operator actions still happen
- Only scheduled background jobs stop firing

**What stops:**
- All Reddit / HN / PH / IH / review-site ingest jobs
- pipeline.normalize and pipeline.cluster scheduled fires

The pause/resume is **global** — pauses all jobs, not per-job. Per-job control is YAGNI for now.

### Feature 2 — Nav counts

- Each nav link in the inbox page chrome shows a count:
  ```
  pending (N)  |  approved (N)  |  rejected (N)  |  unsure (N)  |  [☐ hide non-software (N)]
  ```
- Counts are computed per page load. At current scale (~168 candidates), 5 COUNT queries are negligible cost.
- The "hide non-software (N)" count shows **how many candidates the filter would hide** (i.e., count of `decision='pending' AND buildability='non_software'`). This is consistent across toggle states — "(N)" always means "matches the filter criteria," not "currently visible."
- Counts on detail-view pages (`/inbox/<id>`) and filtered listing pages (`/inbox/approved` etc.) should match the listing — same nav chrome everywhere.
- No caching. If at scale the queries become slow (>50ms total), add a short cache (~10s) at that point; not now.

### Feature 3 — Candidate merge

This is the larger sub-feature. Worth a separate sub-spec.

**Schema change:**

- New column on `candidates`:
  - `merged_into_id INTEGER NULL FK candidates(id)` — null means "not merged"; non-null means "this candidate was merged into the referenced one."
- Alembic migration, FK-safe per the migration discipline (Part 1 of feedback 029).
- Snapshot DB before migrating.

**Soft-deletion semantics:**

- A merged candidate (one with non-null `merged_into_id`) is *soft-deleted*.
- Inbox listings exclude soft-deleted candidates: `WHERE merged_into_id IS NULL`.
- All listings (pending, approved, rejected, unsure) inherit this filter.
- Detail view at `/inbox/<original-id>` for a soft-deleted candidate redirects to `/inbox/<merged-into-id>` with a flash message "This candidate was merged into [parent]."
- `candidate_signals` rows pointing at soft-deleted candidates get UPDATEd to point at the merged-into candidate at merge time (not via DB cascade — explicit application logic, so it's auditable).

**UI for selection:**

- Each row in the listing has a checkbox (HTML form input).
- A "Merge selected" button activates (or becomes clickable) when 2+ checkboxes are checked.
- Counter near the button: "Merge 3 selected."
- Click triggers a confirmation dialog: "Merge these 3 candidates into one? Their problem statements will be combined by Opus into a new statement. This action is not easily reversible."
- On confirm: POST to `/inbox/merge` with selected IDs; server-side merge logic runs; on success, redirect to the new merged candidate's detail view.

**Merge logic (server-side):**

1. Load the N candidates by ID. Validate: all exist, none are soft-deleted, all belong to the operator's context (single-operator simplification; no auth check beyond the existing Apache basic-auth).
2. Construct an Opus call with a new prompt template `merge_candidates.j2`:

   > You are given N candidate opportunity cards from a SaaS-opportunity funnel. The operator believes these N cards describe the same underlying problem and wants them merged into one.
   >
   > For each candidate, you have: problem_statement, suspected_user, seed_keywords.
   >
   > Produce a single merged opportunity card:
   > - `problem_statement`: a unified statement covering all N inputs. Should be more general than any individual input if they describe variations of the same problem; specific enough to be actionable.
   > - `suspected_user`: the user description that best encompasses all N inputs (often the broader/more general one).
   > - `seed_keywords`: combined and deduplicated set, max 8 keywords.
   > - `buildability`: re-assessed for the merged problem (same 4-value scheme: high/medium/low/non_software).
   > - `buildability_rationale`: 1-2 sentences as before.
   >
   > [List the N candidates' fields here]

3. Parse Opus response into a new `IdeaCard`-shaped object.
4. **Persist the merge in a single transaction:**
   - INSERT new `candidate` row with the merged fields. Decision = `pending` (operator re-triages the merged version per HITL durability).
   - UPDATE all `candidate_signals` rows where `candidate_id IN (selected_ids)` to point at the new candidate's ID.
   - UPDATE each selected candidate to set `merged_into_id = new_candidate_id`.
   - Compute new composite weight: SUM of contributing signals' `social_proof_weight` (computed in-app, not via Opus).
   - Write a row to a small audit log if the schema has one (or extend `approvals` if appropriate; details below).

5. Return the new candidate's detail view URL.

**Audit trail:**

- The `merged_into_id` chain itself is the audit (which candidates were merged where, and when via `candidates.created_at`).
- Optionally: extend `approvals` with a new decision value `'merged'` recording the operator action with timestamp and the list of source-candidate IDs as notes. This serves as the "what action did the operator take" log alongside approve/reject/unsure/merge.

**Decision state of the merged candidate:**

- Always `pending`. Per the HITL durability principle from feedback 016: merging is itself a decision-changing action that requires fresh operator review. Forces re-triage of the combined statement.
- Edge case: if merging includes a previously-rejected candidate, the rejection is preserved (the soft-deleted row keeps its `decision='rejected'`). The new merged candidate is `pending` and gets re-triaged. No silent decision-flipping.

**Reversibility:**

- v1 is **not reversible**. The `merged_into_id` audit trail is sufficient for forensic recovery if needed.
- Future feature (if friction arises): an "unmerge" action that breaks the merge. Out of scope for this PR.

**Cost:**

- One Opus call per merge: ~1-2k input tokens, ~300 output tokens → ~$0.013/merge
- At typical use (a few merges per triage session), monthly cost rounds to zero
- Mental cap on a single merge action: $1 (which would require an absurd 70+ candidates merged at once)

## What's NOT in scope

- Per-job scheduler pause (only global pause/resume)
- Caching of nav counts
- Auto-detection of merge candidates ("these look similar, want to merge?")
- Unmerge action
- Merge across decision states with custom inheritance rules (always pending)
- Drag-and-drop merging UI (checkboxes only)
- Bulk approve/reject (different action; not part of this turn)

## Tests

- Unit: pause endpoint calls `scheduler.pause()`; resume calls `scheduler.resume()`; status indicator reflects the state.
- Unit: `scheduler_runs` rows for `ops.manual_pause` and `ops.manual_resume` log correctly.
- Unit: nav counts query produces correct counts per decision value.
- Unit: nav counts handle the hide-non-software filter correctly.
- Migration: data-preservation test for `merged_into_id` column (per the new discipline; parent + child seeded, FKs verified post-migration).
- Unit: merge logic runs in a single transaction; partial failures don't leave inconsistent state.
- Unit: merging redirects existing `candidate_signals` to the new candidate.
- Unit: merged candidates have correct soft-deletion (excluded from listings).
- Unit: detail view of soft-deleted candidate redirects to merged-into candidate.
- Unit: merge of N candidates where one is rejected — rejected stays rejected (soft-deleted with original decision preserved); new candidate is pending.
- Unit: composite weight of merged candidate equals SUM of contributing signal weights.
- Unit: merge button only activates with 2+ checkboxes checked.
- Pre-merge empirical (in worktree): merge 2-3 actual candidates from the live DB; eyeball the merged problem_statement quality. If consistently poor, tune the merge prompt.

## Documentation updates (same PR)

1. `docs/tasks/014-fix-2.md` — new task file
2. `docs/operator/SETUP.md` — Stop/Start scheduler usage; merge action usage
3. `CLAUDE.md → Lessons Learned` — no new entry from this task
4. `docs/orchestrator/INDEX.md` — row 031 → answered

## Implementation order

1. Snapshot DB before any migration: `bash scripts/db_snapshot.sh`
2. Branch `feature/task-014-fix-2-controls-counts-merge`
3. Alembic migration with data-preservation test (the `merged_into_id` addition)
4. Backend: scheduler pause/resume endpoints + status query
5. Backend: nav counts query (helper function reused across templates)
6. Backend: merge logic + Opus prompt template
7. Frontend: checkboxes + merge button (HTMX)
8. Frontend: counts in nav (template update)
9. Frontend: scheduler status + buttons in /ops
10. Tests for all the above
11. Empirical pre-merge check: run merge against 2-3 live candidates in a worktree; verify output quality
12. Open PR

After merge:
13. Operator snapshots live DB
14. Operator pulls main in /workspace
15. Operator runs `alembic upgrade head`
16. Operator verifies in browser: counts appear, pause/resume buttons work, checkboxes appear, merge action works on 2 selected candidates

## Specific questions or risks

1. **Pause state across container restarts.** APScheduler's SQLAlchemyJobStore *should* persist this; verify in implementation. If it doesn't, the minimal-additional-state approach: a tiny `runtime_state` key/value table; pause sets `scheduler_paused=true`; lifespan reads on startup. Don't over-engineer.

2. **Merge button when filter is active.** If "hide non-software" is checked and the operator merges two visible candidates, one of which is high-buildability and another non_software (but the filter hides non_software ones)... wait, that case can't happen because filtered-out candidates aren't visible to select. Non-issue. If selection from multiple filtered states is added later (cross-status merge), this becomes a real question.

3. **Merge across decision states.** Operator could in principle merge a pending with an approved if both are visible. Approved candidate's `decision='approved'` is preserved in the soft-deleted row; new merged candidate is pending. Acceptable. Worth noting in UI: confirmation dialog mentions "merging across decisions; the merged candidate will be pending."

4. **Edge case: 1-candidate "merge."** Merge button should require 2+ selections. Single-checkbox merge is meaningless; disable the button.

5. **Edge case: merging soft-deleted candidates.** Should be impossible by UI design (they're excluded from listings). Server should validate anyway: if any selected candidate has non-null `merged_into_id`, reject with 400.

6. **Merge prompt quality.** Step 11's empirical check is the gate. If the merged problem_statements feel low-quality (over-general, lossy, or off-topic), the prompt needs tuning before merge feature ships. Don't ship blind.

## Relevant files

Code under change:
- `apfun/models/candidate.py` — `merged_into_id` column
- `migrations/versions/NNN_add_merged_into.py` — new migration
- `apfun/web/routes/ops.py` — pause/resume endpoints + status
- `apfun/web/routes/inbox.py` — counts helper, merge POST endpoint
- `apfun/web/templates/_base.html` or wherever the nav lives — counts in nav
- `apfun/web/templates/inbox/listing.html` — checkboxes + merge button
- `apfun/web/templates/inbox/detail.html` — soft-deleted redirect logic
- `apfun/web/templates/ops.html` — pause/resume buttons + status
- `apfun/llm/prompts/merge_candidates.j2` — new template
- `apfun/pipeline/merge.py` — new module for merge logic

Docs:
- `docs/tasks/014-fix-2.md` — new
- `docs/operator/SETUP.md` — usage updates
- `docs/orchestrator/INDEX.md` — row 031 → answered

## Meta note

Three features in one task is more bundling than usual, but they're all operator-control UX of similar shape. The bundling avoids three small PRs and lets you experience all three improvements together in one operator session post-merge.

If any one of these turns out to be larger than expected during implementation, the cleanest split is: pause/resume + counts in one PR (small, additive), merge in a separate PR (larger, schema change). Implementer's call.

## Empirical validation note

After merge, the operator should:
1. Test pause/resume: pause scheduler, verify in `/ops` that no new ingest jobs fire for 10-15 minutes, resume, verify jobs resume. Quick smoke test.
2. Test counts: navigate between inbox views, verify counts match what you see.
3. Test merge: pick 2-3 actual duplicates from the inbox (you probably already have some in mind based on the triage), merge them, eyeball the result. If merge quality is bad, open a follow-up turn to tune the prompt.

These three checks together take 10-15 minutes. They're the gate between "feature shipped" and "feature works for real."
