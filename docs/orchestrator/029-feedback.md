# Feedback 029 — post-incident response + buildability layer task spec

**Date:** 2026-05-24
**Responds to:** Request 029 (post-incident migration safety) + operator decision on buildability filter (Option A + auto-backfill)
**Outcome:** Four directional answers to the post-incident questions. New task spec (request 030) for buildability layer with auto-backfill of existing candidates.

# Part 1 — post-incident response

## On the incident itself

The recovery shape was correct — re-cluster from intact source data, accept the lost decisions, move on. ~half a day of triage decisions is a real cost but a survivable one. The right framing: this is exactly the kind of incident the orchestrator pattern is designed to absorb without project-ending consequences. Source data was intact because the ingest layer is durable; derived layers (candidates, approvals, links) were reconstructable because the derivation logic exists.

The framing worth recording for future operators (us, mostly): **durable layers vs reconstructible layers**. Raw signals and signal_text are *durable* (source of truth, persisted from external systems). Candidates, approvals, llm_runs, scheduler_runs are *reconstructible-or-lossy* (derived or operational). Backup discipline should match this asymmetry — protect the durable layer at all costs, accept that reconstructible layers are recoverable via re-running pipelines.

The lost decisions (7 approved + 11 rejected with notes) hurt because they were *operator-time* artifacts, not derivable. That's the asymmetry that demands real backup discipline.

## Q1 — Migration data-preservation convention: confirmed, with refinement

**Hard CLAUDE.md convention.** Bless your wording with one strengthening:

> **Migration data-preservation discipline.** Every Alembic migration touching a table with `ON DELETE CASCADE` or `ON DELETE SET NULL` children (or any table whose recreation can trigger them via `render_as_batch`) must be **data-preservation-tested**, not just schema-tested. The test seeds parent + N child rows, applies the migration, and asserts:
>
> 1. Parent row count unchanged.
> 2. Child row count unchanged.
> 3. Foreign keys still resolve.
> 4. Cascading-nullable columns (e.g., `llm_runs.candidate_id`) preserved.
>
> An empty-DB validation is insufficient — the failure mode requires real child rows to surface. The test goes in `tests/integration/test_migration_fk_safety.py` (or a sibling file per migration) and runs in `make test-all`.
>
> Beyond that: if a migration's diff includes `op.batch_alter_table(...)` against a table referenced by any FK, the PR author must explicitly confirm "data-preservation tested" in the PR description before merge.

Add the convention; PR #27 already covers the immediate gap.

**CI gate question:** yes, eventually — when task 023 (GitHub Actions CI) lands, the `make test-all` workflow should include `test_migration_fk_safety.py`. But that test requires real API credentials only for *other* tests in that suite, not for this one. Worth a CI workflow note: migration-FK-safety tests run on every PR even when other `make test-all` tests are gated. Implementer's call on how to structure the workflow split.

## Q2 — Backup discipline: yes, formalize

The incident's "unrecoverable in practice" framing is the real lesson. Recoverable-in-principle doesn't matter if no backup exists at the moment data is needed.

**Adopt as convention** (add to CLAUDE.md):

> **Pre-migration snapshots.** Before any `alembic upgrade` against `data/apfun.db`, take a snapshot via `scripts/db_snapshot.sh`. The script copies the live DB to `data/backups/apfun-<head_rev>-<timestamp>.db`. Snapshots are not committed to git (they're in `.gitignore`).
>
> Retention: keep the most recent 10 snapshots; older ones are pruned automatically by the script. At ~1-10 MB per snapshot (current DB scale), 10 snapshots is well under 100MB on the host filesystem.

**Implementation as a small chore PR** (not bundled with 030 below — keep it focused):

- `scripts/db_snapshot.sh` — bash script, idempotent, prunes old snapshots
- `data/backups/` in `.gitignore`
- `docs/operator/SETUP.md` documenting "run snapshot before any migration"
- Optional: integrate into a `make snapshot` target

This sits in the operator workflow, not the application code. Claude Code can write the script; the operator runs it as part of the deploy ritual.

**Beyond per-migration snapshots:** worth considering a daily cron-style snapshot inside the container (via APScheduler — same pattern as ingest jobs). Out of scope for this turn but worth noting; open as a future small task if you want it.

## Q3 — Dev-runtime workflow: formalize the worktree approach

Your adopted workaround is correct. Worth formalizing because it materially shapes how Claude Code operates day-to-day.

**Add to CLAUDE.md as a new section "Dev runtime workflow":**

> **`/workspace` is the deployment surface, not the development surface.**
>
> The container's bind-mounted `/workspace` runs uvicorn with `--reload --reload-dir /workspace/apfun`. Any file change in `/workspace/apfun/*.py` reloads the live service. Any branch checkout there transiently runs unmerged code against production.
>
> Discipline:
>
> 1. **`/workspace` stays permanently on `main`.** No branch checkouts there; no editing files there mid-task.
> 2. **All branch work happens in git worktrees outside the watched path** — typically `/tmp/apfun-<task>` with its own `uv` venv. Tests, pyright, ruff all run in the worktree against branch code without touching `/workspace`.
> 3. **Deploy = `git pull` on `main`.** The operator (or Claude Code, post-merge) runs `git pull` inside `/workspace`. uvicorn reloads with the new code automatically.
> 4. **Schema migrations are *also* run in `/workspace`**, on `main`, post-pull — because they touch the live `data/apfun.db`. Per the snapshot convention above: snapshot first, then migrate.
>
> The alternative — drop `--reload` and use deliberate restart-on-deploy — was considered. Rejected because: (a) the credential-restart cost (Claude Code re-login, gh re-auth) per deploy was the original reason `--reload` got enabled; (b) the worktree workflow is a lighter discipline than recurring restart pain.

This is mostly Claude-Code-facing documentation, but the operator needs to know point 4 (migrations in /workspace, not in worktrees, because the live DB lives there).

## Q4 — Lost decisions: accept, no follow-up

Re-triage from scratch. ~half a day of operator work isn't worth a forensic-recovery exercise even if one were possible. The current 168 candidates are pending; the operator works through them with the (now-improved) inbox UI.

One small operational nicety: the now-lost approvals included notes you'd written about *why* you rejected/approved each. Those notes were operator-time artifacts. Going forward, the new notes field (task 014-fix-1) preserves them durably *and* the backup discipline above protects them across migrations. Belt and suspenders. The loss this time was bad luck before either safeguard was in place.

# Part 2 — buildability layer task spec (request 030)

This is its own task; treat the second-half of this file as a new orchestrator request that Claude Code reads and acts on independently.

---

# Request 030: task 015 — buildability filter via cluster.j2 extension

**Date:** 2026-05-24
**Context:** Operator observed during triage that several candidates (remote-jobs market dynamics, human-vs-AI cultural concerns, indie search engines) are real complaints from real people but don't map to a software/SaaS/service that could plausibly be built. Current Stage 1 gates on *"is there a complaint?"* — not on *"is the complaint software-addressable?"* Adding a buildability layer reduces operator triage noise while keeping all candidates visible (no hard auto-filter).

After exploring four design options (extend existing Opus call vs. separate Opus pass vs. silent filter vs. operator hint), the chosen approach is **Option A: extend the existing `cluster.j2` Opus call** with a buildability assessment as part of the same output schema. Lowest marginal cost; cleanest observability; no extra LLM calls.

## Goal

1. Every new candidate from cluster.py has a buildability assessment attached (4-value categorical + 1-2 sentence rationale).
2. All existing candidates (the 168 currently in the DB) get a one-time backfill so the inbox UI is consistent across new and old.
3. The inbox UI surfaces buildability as a color-coded label per candidate; default view shows all (no auto-filter); operator can toggle to hide non-software candidates if they choose.
4. Buildability does NOT feed into composite weight (which stays social-proof-only).

## Scope

**In scope — schema:**

- New columns on `candidates` (Alembic migration, FK-safe per the new migration discipline):
  - `buildability ENUM('high', 'medium', 'low', 'non_software') NULL` — null means "not yet assessed"
  - `buildability_rationale TEXT NOT NULL DEFAULT ''` — Opus's 1-2 sentence reasoning
  - `buildability_assessed_at DATETIME NULL` — when the assessment was made (helps with audit + future re-assessment policy)

  Per migration discipline: must be data-preservation-tested (parent + child seeded; assert all preserved post-migration).

- Snapshot the DB before applying this migration via `scripts/db_snapshot.sh`.

**In scope — cluster.py + cluster.j2:**

- Update `IdeaCard` (or whatever the Pydantic schema is called) with the two new fields:
  ```python
  class IdeaCard(BaseModel):
      problem_statement: str
      suspected_user: str
      seed_keywords: list[str]
      contributing_signal_ids: list[int]
      # New:
      buildability: Literal["high", "medium", "low", "non_software"]
      buildability_rationale: str  # 1-2 sentences explaining the judgment
  ```

- Update `apfun/llm/prompts/cluster.j2` to add the buildability assessment as a *separate reasoning step* after clustering:
  - Step 1 (existing): identify the cluster's problem_statement, suspected_user, etc.
  - Step 2 (new): for each cluster identified, assess buildability via the prompt instruction below.

  Suggested prompt addition (adapt to existing prompt's style):

  > For each cluster you've identified, additionally assess **buildability**: could a small team build a software, SaaS, or service product that meaningfully addresses this problem? Return one of:
  >
  > - `high`: Clearly software-addressable. Existing analogous products exist (or used to exist) for related problems. A founder could plausibly start building this next week.
  > - `medium`: Partially software-addressable. Some aspects (workflow, data, communication) can be built; some aspects (regulation, network effects, hardware) require non-software complements.
  > - `low`: Software is a minor part of the solution. The real problem requires human judgment, regulation, capital, or scale that software alone won't provide.
  > - `non_software`: Not a software/SaaS/service opportunity at all. The complaint is cultural, regulatory, philosophical, or about a problem where any software involvement would be tangential.
  >
  > Include a 1-2 sentence rationale explaining the buildability judgment. Be specific — name the software-addressable components if `high`/`medium`, or name what's missing if `low`/`non_software`.

  The prompt should make clear: buildability is a *separate* judgment from cluster quality. A weak cluster about a software-addressable problem is still `high` buildability (but might fail other quality bars). A strong cluster about a non-software problem is still `non_software`.

- Update cluster.py's persistence layer to write the new fields.

**In scope — backfill of existing candidates:**

This is the operationally-interesting part. The 168 existing candidates need buildability assessments.

Approach: a new script `scripts/backfill_buildability.py` that:

1. Queries all candidates where `buildability IS NULL`.
2. For each, constructs a "single-cluster" Opus prompt (slightly different from cluster.j2 because there's no clustering work to do — just the buildability assessment).
3. Runs Opus per-candidate, parses the buildability + rationale, persists.
4. Idempotent — re-running skips already-assessed candidates.
5. Reports total candidates assessed + total cost at the end.

The backfill prompt can be a sibling template `apfun/llm/prompts/buildability_only.j2`:

> Given this opportunity card from a SaaS-opportunity funnel, assess **buildability** only:
>
> Problem statement: {{ problem_statement }}
> Suspected user: {{ suspected_user }}
> Seed keywords: {{ keywords }}
>
> [Same 4-value scheme + rationale instructions as the cluster.j2 addition]

Why a separate template instead of reusing cluster.j2: the backfill has no clustering work to do, just the assessment. Reusing cluster.j2 would waste Opus tokens on irrelevant instructions. Different template = leaner input = cheaper.

**Cost estimate for backfill:**
- 168 candidates × ~500 input tokens (the candidate's existing fields + the assessment prompt) ≈ 84k input tokens
- Opus 4.7 at $5/MTok input ≈ $0.42 for the input side
- Outputs at ~200 tokens each × 168 = 33.6k output tokens × $25/MTok ≈ $0.84
- **Backfill total: ~$1.25** for one-time assessment of all 168 candidates.

Acceptable. Operator should mentally cap the backfill at $5 — if the script approaches that, something's wrong.

**In scope — inbox UI:**

- Each candidate row in the listing displays a color-coded buildability label:
  - `high` → green badge "Buildable"
  - `medium` → yellow badge "Maybe"
  - `low` → orange badge "Unlikely"
  - `non_software` → gray badge "Non-software"
  - `null` (unassessed; should be rare after backfill) → no badge or "Unassessed"
- Detail view at `/inbox/<id>` (from task 014-fix-1) shows the buildability rationale text inline below the label.
- A small toggle filter on the inbox listing: "Hide non-software ☐". When checked, candidates with `buildability='non_software'` are excluded from the listing. Stored as a query param on the URL (e.g., `?hide_non_software=true`) so it's bookmarkable.
- Default: toggle is unchecked (operator sees all). Override per-session.

**In scope — tests:**

- Migration data-preservation test (per the new discipline from Part 1).
- Schema validation: `IdeaCard` accepts the new fields; old fixtures without buildability should fail validation (forces awareness of the schema change).
- Cluster pipeline: stub Opus client returning a known buildability; assert candidates persisted with correct values.
- Backfill script: stub Opus client; assert idempotency (running twice doesn't re-assess); assert cost reporting at end.
- Inbox listing: candidates with each buildability value render correct badges.
- Inbox filter: `?hide_non_software=true` excludes non_software candidates; default shows all.
- Inbox detail: rationale renders below the label.

**Out of scope:**

- Auto-rejecting non_software candidates. Operator-controlled filter only. Auto-reject is only worth considering after several weeks of data showing the operator rejects 100% of non_software candidates consistently.
- Periodic re-assessment of buildability for existing candidates. Single one-time backfill; future candidates get assessed at cluster time.
- Feeding buildability into composite weight. Keep weight social-proof-only.
- Click-to-filter on the badge (e.g., "show me only the green ones"). Future feature if friction surfaces.
- Source-specific buildability tuning (e.g., "review-site candidates should default to higher buildability"). No evidence for this yet; don't pre-engineer.

## Implementation order

1. Snapshot DB: `bash scripts/db_snapshot.sh` (per Part 1 backup discipline — assuming the snapshot script lands first).
2. Branch `feature/task-015-buildability`.
3. Alembic migration with data-preservation test.
4. Update `IdeaCard` schema + `cluster.j2` prompt + persistence.
5. Write `scripts/backfill_buildability.py` + `buildability_only.j2` template.
6. Update inbox listing template (badges + filter toggle).
7. Update inbox detail template (rationale).
8. All the tests.
9. Run unit tests; verify all pass.
10. **Pre-merge empirical step:** run the backfill script against the local DB in a worktree (per the dev-runtime workflow from Part 1). Verify it produces sensible buildability values for ~5-10 candidates manually. If Opus is misjudging consistently, tune the prompt before merging.
11. Open PR.

After merge:
12. Operator: snapshot the live DB.
13. Operator: `git pull` in `/workspace` to apply the code change.
14. Operator: run `alembic upgrade head` to apply the migration.
15. Operator: run `scripts/backfill_buildability.py` to populate buildability on the 168 existing candidates.
16. Operator: refresh `/inbox`; verify badges show; spot-check 5-10 candidates' rationales.

## Tests

Already covered in scope. Additional integration-style validation in step 10 above (pre-merge) and step 16 (post-merge).

## Documentation updates (same PR)

1. `docs/tasks/015-buildability.md` — new task file with this spec content.
2. `docs/orchestrator/INDEX.md` — row 030 → answered after PR merges.
3. `docs/operator/SETUP.md` — backfill instructions for fresh installs (run after first cluster).
4. `CLAUDE.md` — no convention changes from this task itself; the conventions from Part 1 (migration safety, backup, dev-runtime) cover it.
5. Bundle the **convention updates from Part 1** into the same PR if they haven't landed yet. If they've already shipped (e.g., as a separate chore PR), reference them.

## Specific questions or risks

1. **Prompt impact on clustering quality.** Asking Opus to do *two* judgments in one call risks degrading the first one (clustering). The mitigation is the prompt's "separate reasoning step" framing. Validate empirically in step 10: re-run clustering against the runbook 001 fixtures and compare cluster quality before and after the buildability addition. If quality degrades noticeably, fall back to Option B (separate Opus call). Don't ship blind.

2. **Backfill costs more if some candidates require multiple retries.** The JSONParseError retry pattern from task 010 applies — if Opus returns malformed JSON, retries kick in. Worst case: 3x cost for some candidates. Mental budget: $5 for the backfill; abort and investigate if the script approaches that.

3. **Re-assessment policy for existing candidates.** What if buildability prompts get tuned later — should existing candidates be re-assessed? For now: no. The `buildability_assessed_at` column tracks when each was assessed; future operator-driven re-assessment (via a script flag like `--re-assess-older-than=2026-06-01`) is possible but not in this PR.

4. **What if the operator approves a non_software candidate anyway?** That's fine — buildability is a hint, not a gate. The decision stays the operator's. Worth noting in the PR description so reviewers don't expect buildability to constrain decisions.

5. **Coupling with task 014-fix-1.** Task 014-fix-1 added the candidate detail view. This PR extends that detail view to show buildability rationale. If 014-fix-1 hasn't merged yet, sequence after it. If both ship close together, the second PR's diff is the detail-view rationale addition (a few lines).

## Relevant files

Code under change:
- `apfun/models/candidate.py` — new columns
- `migrations/versions/NNN_add_buildability.py` — new migration
- `apfun/llm/schemas.py` (or wherever IdeaCard lives) — schema extension
- `apfun/llm/prompts/cluster.j2` — prompt addition
- `apfun/llm/prompts/buildability_only.j2` — new template (for backfill)
- `apfun/pipeline/cluster.py` — persist new fields
- `scripts/backfill_buildability.py` — new
- `apfun/web/templates/inbox/listing.html` — badges + filter toggle
- `apfun/web/templates/inbox/detail.html` — rationale display
- `apfun/web/routes/inbox.py` — filter param handling

Docs:
- `docs/tasks/015-buildability.md` — new
- `docs/operator/SETUP.md` — backfill instructions
- `docs/orchestrator/INDEX.md` — row 030 → answered

## Empirical validation

Pre-merge (step 10): run backfill against 5-10 candidates in a worktree, eyeball the values, tune prompt if needed.

Post-merge (step 16): operator spot-checks 5-10 candidates' buildability + rationale in the live inbox. If the assessments feel wrong (e.g., things you'd build are marked non_software, or vice versa), open a follow-up turn to tune the prompt. Expect ~80%+ alignment with operator instinct; below that suggests prompt refinement.

## Meta note

This is the first stage where Stage 1 outputs a *judgment* not derived from social proof. Worth recording for the project's mental model: Stage 1 has always been about *clustering*; buildability is the first *evaluation* layer. Future stages (2 demand check, 3 competitive scrape, 4 saturation scoring, 5 differentiation synthesis) all add evaluation layers on top of clustering. Buildability is the smallest possible first step in that direction.

The right design heuristic going forward: each evaluation layer should be (a) observable (operator sees its judgment), (b) reversible (operator can override or ignore), (c) cheap enough that it doesn't dominate costs (well under 30% of total LLM spend per stage).

Buildability hits all three. The pattern transfers to future stages.
