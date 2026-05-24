# 025 — Buildability layer (Stage 1 evaluation)

**Goal:** every candidate carries a buildability assessment — *is this complaint
software-addressable?* — surfaced as a color-coded inbox badge with an optional
"hide non-software" filter. It's a hint, never a gate: the operator can still
approve a `non_software` candidate, and buildability does NOT feed the composite
weight (which stays social-proof-only).

**Complexity:** M

Depends on: 010 (clustering), 014-fix-1 (inbox detail view).

> **Numbering note.** Orchestrator request 030 (Part 2 of `029-feedback.md`)
> titled this "task 015". That number was already taken by `015-dataforseo-client.md`,
> so this lands as **025** (next free task number; the file-number sequence ran
> through 024). The branch is `feature/task-025-buildability`. Per the
> "verify referenced affordances before assuming they exist" convention.

This is the **first Stage 1 output that is a judgment, not derived from social
proof** — clustering has always been mechanical grouping; buildability is the
first *evaluation* layer. The pattern (observable, reversible, cheap) is meant
to transfer to later evaluation stages (016 competitive, 017 scoring, 018
synthesis).

## Approach

**Option A** of four considered: extend the existing `cluster.j2` Opus call with
a buildability assessment as a second reasoning step in the same output schema.
Lowest marginal cost (no extra LLM call), cleanest observability. Existing
candidates (created before this layer) get a one-time backfill.

## Deliverables

**Schema** (`apfun/models/candidate.py` + migration `4e8f1a2b9c3d`):
- `buildability` enum `('high','medium','low','non_software')`, nullable (NULL =
  not yet assessed). CHECK constraint via `check_enum_sql` — NULL passes
  (`NULL IN (...)` is NULL, which SQLite treats as a satisfied CHECK).
- `buildability_rationale TEXT NOT NULL DEFAULT ''` — Opus's 1-2 sentence reasoning.
- `buildability_assessed_at DATETIME NULL` — audit + future re-assessment policy.
- Migration is **data-preservation-tested** (per CLAUDE.md → Migration
  data-preservation discipline): `tests/integration/test_migration_buildability_fk_safety.py`
  seeds a parent + `candidate_signals` + `approvals` + a candidate-linked
  `llm_runs` row, runs the batch recreate, and asserts all four survive
  (including the `ON DELETE SET NULL` on `llm_runs.candidate_id`).

**Cluster pass** (`apfun/pipeline/cluster.py` + `apfun/llm/prompts/cluster.j2`):
- `IdeaCard` gains required `buildability` + `buildability_rationale` (no
  defaults — a response omitting them fails validation, forcing awareness).
- `cluster.j2` adds the buildability assessment as a *separate* reasoning step,
  explicitly framed as independent from cluster quality.
- New candidates persist all three fields; a dedup-key match does NOT re-assess
  (one-time, first-creation judgment).
- `"buildability"` added to `JUDGMENT_TASKS` + `DEFAULT_EFFORT` (medium — a
  bounded 4-way classification, same tier as `cluster`).

**Backfill** (`scripts/backfill_buildability.py` + `apfun/llm/prompts/buildability_only.j2`):
- One-time assessment of candidates where `buildability IS NULL`. Uses a leaner
  single-candidate template (no clustering instructions to pay for).
- Idempotent (skips already-assessed; per-candidate commit → crash-resumable).
- Cost guard: aborts if cumulative `buildability` cost exceeds `--budget`
  (default $5; orchestrator estimate ~$1.25 for ~168 candidates).
- `--dry-run` lists what would be assessed without spending; `--limit N` caps a run.

**Inbox UI** (`apfun/web/routes/inbox.py` + templates + `app.css`):
- Color-coded badge per candidate: `high`→"Buildable" (green), `medium`→"Maybe"
  (yellow), `low`→"Unlikely" (orange), `non_software`→"Non-software" (gray);
  unassessed → no badge.
- `?hide_non_software=true` query param (bookmarkable) excludes non_software
  candidates from the listing; default shows all. Toggle link in the filter nav.
- Detail view (`/inbox/<id>`) renders the rationale below the badge.

## Acceptance
- New candidates from a cluster run carry buildability + rationale + assessed_at.
- The backfill assesses every NULL candidate, is idempotent, and reports cost.
- Inbox shows the right badge per value; `?hide_non_software=true` filters;
  detail view shows the rationale.
- Migration preserves child rows on a seeded DB.

## Out of scope
- Auto-rejecting non_software candidates (operator-controlled filter only).
- Periodic re-assessment of existing candidates (single one-time backfill;
  `buildability_assessed_at` enables a future `--re-assess-older-than` flag).
- Feeding buildability into composite weight.
- Click-to-filter on the badge; source-specific buildability tuning.

## Risks (per request 030)
- **Two judgments in one call may degrade clustering.** Mitigated by the
  prompt's "separate reasoning step" framing; validate empirically (replay
  clustering before/after). Fall back to a separate Opus call (Option B) if
  quality drops noticeably.
- **Backfill retry cost.** JSONParseError retries (task 010 pattern) can 3× a
  candidate's cost; the $5 budget guard bounds the blast radius.
- **buildability is a hint, not a gate** — approving a non_software candidate is
  fine; the decision stays the operator's.

## Conventions referenced (Part 1 of feedback 029, shipped separately)
Migration data-preservation discipline, pre-migration snapshots, and the
dev-runtime worktree workflow landed in the `chore/post-incident-conventions`
PR. This task follows them (snapshot before the migration; backfill run in a
worktree, not against `/workspace`).
