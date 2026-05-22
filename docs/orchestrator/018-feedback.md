# Feedback 018 — empirical input received, routing decision: 013 + 014

**Date:** 2026-05-22
**Request:** 018-stage1-empirical-input.md
**Outcome:** Routing confirmed (013+014 bundled). Seven question answers; three additional observations the operator didn't ask about; action items split between the upcoming PR and follow-up tracking.

## On the case study

The runbook-001 session is now the canonical proof that empirical-input-first works as a discipline:

- **3 production bugs** caught in the first hour (SAVEPOINT-scoped rollback, JSON fence wrapping, Opus 4.7 thinking-API migration).
- All three would have silently degraded data or crashed the scheduler.
- All three had survived weeks of synthetic-test coverage.

This validates the feedback-017 framing at a strength I didn't fully appreciate at the time. The 30-60 minutes of operator work bought us:
- A routing decision with real data
- 3 production bug fixes
- 11 candidates already in the DB ready to display when 013+014 ships
- Clarity on real cost shapes vs. predicted

The empirical-input-first discipline is now load-bearing for the project's quality. Codify it (Q5 below) and apply at every stage gate.

## Q1 — Routing: 013 + 014 bundled, confirmed

**Take the inbox path.** The 11 candidates are clearly reviewable:

- Problem statements grounded in actual signal text
- Contributing IDs valid
- Suspected users sensible
- Keywords coherent

The *quality* of the underlying ideas varies (#2/5/7 look strong, #6/10 look real-but-unactionable, #4 looks weak), but that's exactly what HITL is for. Stage 1's job is "surface candidates worth a human looking at," not "filter for good SaaS opportunities." By that metric, 11/11 qualify.

This matches the 70%+ reviewable bucket from feedback 017. **Next task: 013 + 014 bundled in one PR.** 013 alone is a UI shell with no real content; bundling 014 makes the merge produce something operationally meaningful (you can look at the 11 candidates already in the DB).

## Q2 — Cost validation: defer re-validation to N=100+

Single-signal-bucket artifact makes per-cluster cost unrepresentative. Don't retune PRICING from this run.

**Sanity check:** $0.013 / 1456 input tokens ≈ $9 per 1M tokens, vs. the verified Opus 4.7 input rate of $5/MTok. Output at $25/MTok adds the rest. Math roughly checks out → per-token PRICING is right; the surprise was *bucket size*, not *rate*.

**Re-validation gate:** first scheduler-driven Stage 1 run (post task 012) where realistic multi-signal buckets exist. Open an orchestrator turn at that point with fresh `llm_runs` aggregates.

## Q3 — Retune trigger under adaptive thinking: drop, with persistence

**Confirm (c) for v1.** Drop the per-call budget warning. Under adaptive effort, Anthropic chooses the spend; if it spends too much, cost aggregates surface that.

**Small addition:** start persisting `effort` to `llm_runs` (single column add via Alembic migration). Doesn't drive any warning yet, but gives you a future column to query against:

- "Are we stuck at `max` effort for cluster?"
- "How does cost/quality differ between `medium` and `high` for `score`?"

Once you have N=100+ rows per task with `effort` recorded, the real retune question becomes visible from cost + retry + output-token distributions across effort levels. Tooling for that lives in task 021 (sources-health panel for LLM budgets, per feedback 006).

## Q4 — Reddit: accept-and-defer, confirmed

Confirm (b). Reddit's free API is genuinely flaky from datacenter IPs; auto-disable + scheduler observability handles it gracefully; OAuth migration is real work that should wait for evidence.

**Caveat to record:** if a particular niche tracked by the funnel turns out to be Reddit-heavy (gaming, certain hardware communities, certain hobby SaaS markets), Reddit becomes load-bearing for that niche specifically. Reopen the OAuth question at that point. Until then, lean on the other four sources.

## Q5 — Lesson Learned in CLAUDE.md: yes, with refined wording

> **Synthetic tests don't catch surface-changing bugs.** Three categories of production bugs survived weeks of synthetic-test coverage and were caught in the first hour of runbook 001 (2026-05-22):
>
> 1. **Transaction-shape bugs** where the test's commit cadence diverged from production's (e.g., SAVEPOINT scope inside a multi-statement batch).
> 2. **LLM-quirk bugs** where the stub doesn't reproduce real-model formatting quirks (e.g., markdown fences around JSON output).
> 3. **Upstream-API-change bugs** where the SDK's deprecations aren't covered by mocked responses (e.g., Opus 4.7's thinking-API migration).
>
> For Stage 1+ work, plan a runbook-shaped empirical session shortly after writing tests. Cost is cheap (30-60 minutes); the alternative is silent production data loss or scheduler crashes.

Add to CLAUDE.md → Lessons Learned with date `2026-05-22`.

## Q6 — Cache wiring: follow-up, not bundled

**Defer.** 013+014 is a UI task; bundling an LLM optimization is scope creep. Don't proliferate task numbers for a one-line change — track as a known-pending optimization in `docs/tasks/010-stage1-clustering.md` Notes section.

Two preconditions before wiring:

1. Prompt template stable (no in-flight prompt iteration).
2. N=100+ rows in `llm_runs` where cache impact can be measured.

Likely lands in the same calendar week as the post-scheduler re-validation from Q2. Bundle there.

## Q7 — Singleton buckets at N=11: data-shape, not a bug

HN's "Ask HN" / "Show HN" content is by nature one-of-a-kind problem statements. The clustering Stage 1 is designed for kicks in when *many users complain about the same thing*. Across 11 posts spanning recruiting/dev-tools/security/social/search/finance, you wouldn't expect overlap.

Real clustering pressure will come from:
- **Reddit vertical subs** (recurring complaints about same vendor pain in same vertical)
- **Review-site data** (multiple negative reviews of same product)
- **HN at much higher N** (multiple Ask HN's about same SaaS gap over weeks)

**Re-validation gate** ("if Stage 1 keeps producing 1:1 signal-to-candidate at N=100+") is correct. Track in next-cluster-PR description; do nothing now.

## Three observations the request didn't surface

### Vertical label drift

Candidates #1 (`recruiting`) and #4 (`hiring`) are the same vertical with different free-form labels. Haiku is emitting verticals as freeform strings; this proliferates and breaks downstream filtering/grouping.

**Don't fix now** — at N=11 you can't see how bad it gets. Add to the same N=100+ re-validation item: *"if vertical labels exceed ~20 unique values, constrain to a fixed taxonomy."* The fix when it matters is a `VERTICALS = Literal[...]` allowlist in the dedup-signal schema with "other" as fallback.

### Operational discipline at every stage

The pattern from this runbook applies beyond Stage 1: **any code path with real external dependencies (network APIs, SDK quirks, transaction boundaries) needs runbook-shaped validation before being trusted in production.** Synthetic tests are necessary but not sufficient.

**Project-level convention** (add to CLAUDE.md → Conventions, or as a Lesson Learned sibling):

> Every pipeline stage (1-5) requires a `docs/operator/runbooks/NNN-<stage>-first-pass.md` before its scheduler integration. The runbook is the empirical-input-before-production gate. Cost is small (30-60 minutes operator time); the discipline catches transaction, LLM-quirk, and upstream-API bugs that synthetic tests miss.

### The orchestrator-pattern itself earned its keep this turn

Worth recording for future:

- The runbook-as-empirical-input convention (feedback 017) is now a load-bearing project capability.
- The branch-precommit routing matrix worked exactly as designed — clear data → fast decision.
- The `llm_runs.error` truncated-response logging from feedback 016 Q3 diagnosed Bug #2 directly. The design earned its keep.

Each of these is a small architectural decision made in a feedback turn that compounded into real project value.

## Action items for the 013+014 PR

1. **Task 013 base** per spec — chrome, Tailwind via standalone CLI, HTMX, nav.
2. **Task 014 inbox endpoint** — `/inbox` lists `decision='pending'` candidates ordered by composite weight (sum of contributing signals' `social_proof_weight`) or by recency for v1.
3. **Approve/reject HTMX mutations** — POST `/inbox/<id>/approve` and `/inbox/<id>/reject`, write `approvals` row, update `candidates.decision`. Optional comment field.
4. **`signals_since_rejection` surface** — for rejected candidates with new signals: `candidate_signals.created_at > approvals.decided_at`, displayed as "X new signals since rejection — re-review?" Decision does NOT auto-flip (per feedback 016 Q5).
5. **`effort` column on `llm_runs`** (Q3) — Alembic migration + wrapper persists per call.
6. **Lesson Learned in CLAUDE.md** (Q5).
7. **Update task 010's Notes** with the three N=100+ tracking items:
   - Cache wiring (Q6)
   - Vertical label drift
   - Singleton-bucket re-validation (Q7)
8. **Operator-runbook convention** in CLAUDE.md per the second observation above.

## Container hygiene (separate from 013+014)

9. Add `sqlite3` to the Dockerfile system-deps block. Small, useful for debugging.
10. Verify (or add) the post-rebuild checklist in `/srv/claude/apfun.online/README.md` from feedback 015 — sounds like it never got added.

## Next step

013+014 in one PR. Big-ish — UI scaffold + first interactive endpoint + migration + Lesson Learned + Notes updates — but not gnarly.

When it merges, you'll be able to **open a browser and look at the 11 candidates** from this runbook session and decide which to approve. That's the first meaningful HITL exercise in the project's history — the moment when the funnel starts being a tool you use rather than infrastructure you build.

After 013+014, the natural next move is **task 012 (scheduler)** to get Stage 1 running automatically on a cadence. Then live data accumulates, the retune trigger gates from Q2/Q3/Q6/Q7 start firing, and the funnel is operationally complete through Stage 1.

## Meta note

This is the most data-rich orchestrator turn so far, and arguably the most valuable. The pattern — empirical input from a runbook → orchestrator routing with concrete evidence → action items grounded in real findings — should be repeated at every stage gate (1, 2, 3, 4, 5).

If the project ever feels stuck on "which task next" or "is the current quality good enough," the answer is: **run a small empirical session, then decide.** The runbook overhead is cheaper than the alternative every single time we've used it.
