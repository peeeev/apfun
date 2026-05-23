# Feedback 022 — Reddit access closed-loop, fixture split

**Date:** 2026-05-22
**Request:** 022-reddit-proxy-green.md
**Outcome:** Runbook 003 confirmed working; Reddit returns to rotation. One small follow-up PR for the fixture split. Routing review approaching.

## Closing the loop

Three orchestrator turns (020 → 021 → 022), one wasted code task (005b OAuth), one corrected code task (005c proxy+UA), one runbook execution. End-state: Reddit ingester works from a datacenter IP via residential proxy + browser-mimicking UA pool. Both gating layers (network IP block + frontend UA filter) defeated.

The pattern is the artifact worth recording: this is what the orchestrator structure is *for*. One direct-implementation pass without the multi-turn correction structure would have likely produced either OAuth-only code that doesn't work (and a mystery to debug) or proxy-only code that doesn't work (and a different mystery to debug). The structure caught both blockers; tuition was one wasted task.

## Q1 — Fixture refresh: confirmed your split

Your instinct is exactly right. Two fixtures, two different jobs:

- **Value-asserting tests** need controlled synthetic data. Deterministic; failures = parser bugs.
- **Schema-contract tests** need captured real data. Variable; failures = upstream shape drift.

A single fixture can't serve both. Synthetic-only schema-contract is circular (we'd be checking our synthetic against our parser). Real-only value-asserting is flaky (every refresh breaks unrelated tests).

Action:

1. Add `tests/fixtures/reddit/listing_saas_real.json` from the runbook 003 capture. Header per feedback 010 convention:

   ```json
   {
     "_fixture_meta": {
       "captured": "2026-05-22",
       "source": "GET https://www.reddit.com/r/SaaS/.json via residential proxy + Chrome UA",
       "purpose": "schema-contract test (shape drift detection); value-asserting tests use listing_saas.json (synthetic)"
     },
     "kind": "Listing",
     ...
   }
   ```

2. Strip `_fixture_meta` before validation, same pattern as the Anthropic fixture's `_meta_note`.
3. Point `test_reddit_schema_contract.py` at `_real.json`. Leave `test_reddit_ingester.py` untouched on `_saas.json`.

**Don't update runbook 003 itself** to fix the instruction. Runbooks are records of what happened, not perpetual templates. When a future similar runbook lands (HN fixture refresh, PH refresh, etc.), copy the structure and bake this lesson in fresh.

### New Lesson Learned

Add to CLAUDE.md → Lessons Learned, dated 2026-05-22 (sibling to the existing entry):

> **Fixtures serve two different jobs; one file can't do both.** Value-asserting tests need controlled synthetic data (deterministic; failures indicate parser bugs). Schema-contract tests need captured real data (variable; failures indicate upstream shape drift). When a runbook instruction says "refresh the fixture from real data," check whether the existing fixture is load-bearing for value assertions before overwriting. Safer pattern: two fixtures, one synthetic + one real, each pointed at by tests that want that flavor. Apply to other ingesters as their fixture sets evolve.

The runbook 003 instruction that nearly broke this was an honest oversight in the runbook spec, not a Claude Code bug — the operator caught it during execution, which is the empirical-validation discipline working.

## Q2 — Lesson Learned wording: no change

Existing entry covers the policy-change/multi-cause lesson regardless of outcome. Green-specific addendum would dilute it.

**Optional**, if you want green-outcome context preserved for future-readers: append one line to the tail of the existing entry:

> *...The workaround (residential proxy + browser-mimicking UA pool + full browser header set) succeeded against both layers as of 2026-05-22; runbook 003 closed green.*

Otherwise leave as-is. The point was the discipline change, not the specific Reddit outcome.

## Q3 — Routing review timing: noted, no action now

Acknowledged. The 3-5-cycle / 30-50-candidate routing review (feedback 019: 011 vs 015 vs continued observation) approaches sooner now that Reddit is in rotation alongside HN.

When you open that orchestrator turn, useful artifacts:

- `candidates` count by `decision` (pending / approved / rejected)
- Operator triage observations from actually reviewing the inbox — patterns that get approved, patterns that get rejected, anything surprising
- `llm_runs` cost aggregates — the deferred N=100+ retune gate from feedback 018 finally becoming actionable

**Order matters:** run the scheduler for a few days → accumulate ~30-50 real candidates → triage ~10-15 of them → *then* write the routing turn. That sequence gives the routing decision real signal. Don't pre-write the request.

## Action items

### Small follow-up PR (chore/reddit-fixture-split)

1. Add `tests/fixtures/reddit/listing_saas_real.json` from runbook 003 capture.
2. Update `test_reddit_schema_contract.py` to load `_real.json` with `_fixture_meta` stripped.
3. Add the new "Fixtures serve two different jobs" Lesson Learned to CLAUDE.md.
4. Mark INDEX.md row 022 → answered.

### Operator (whenever)

5. Optionally append the green-outcome line to the existing Lesson Learned. Or leave alone.
6. Let the scheduler run. Trigger a normalize + cluster cycle to fold the 75 captured Reddit signals into candidates.
7. Triage inbox candidates as they accumulate. Note patterns.
8. Open routing-review turn when ~30-50 candidates exist *and* ~10-15 have been triaged.

## Meta note

Two real artifacts emerged from this multi-turn arc beyond the working Reddit ingester:

1. **Search-before-spec discipline** for external-API tasks. Locked in as a Lesson Learned and applies to every future ingester refresh and to task 015 (DataForSEO).
2. **Fixture-purpose discipline** (this turn). Locked in as a sibling Lesson Learned. Applies to every ingester's test suite going forward.

Both compound. The next external-API task that touches a new third party (DataForSEO is the obvious one) will inherit both disciplines without re-derivation. That's the orchestrator pattern's value — turning one-off corrections into project-wide conventions.

The next genuinely hard design surface is task 015. Worth pausing before drafting that task to: web-search current DataForSEO API state, current pricing, current rate limits, *before* writing the spec. The discipline applies to ourselves first.
