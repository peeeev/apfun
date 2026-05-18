# Feedback 005 — LLM wrapper hardening + verify-constants convention

**Date:** 2026-05-18
**Request:** 005-llm-wrapper-hardened.md
**Outcome:** Approved — feedback 004 fully applied, pragmatic call on the 3× pricing miss accepted. New durable convention to bake in. Six concrete follow-ups before/during task 005 and beyond.

## On the 3× pricing miss

You made the right call going pragmatic. The "open a new request" trigger in feedback 004 was aimed at protecting historical `llm_runs` data, not at the verification process itself. Empty table → nothing to backfill → no escalation needed.

The lesson, however, is bigger than pricing.

## New durable convention: verify-constants

Add to `CLAUDE.md` → Conventions:

> All numeric or string constants sourced externally (pricing, API rate limits, model identifiers, third-party-published timeouts, TTLs, etc.) must carry a `# verified YYYY-MM-DD <source-url>` comment. Constants without this annotation are treated as unverified during code review. When you encounter an unverified constant during a task, verify it as part of that task — don't defer.

**No standalone `verify_pricing.py` script.** Standalone audit scripts decay (last-run six months ago, nobody remembers). The convention is the audit: every PR touching an external constant verifies it inline.

## Answers to the six follow-up questions

### Q1 — Pricing verification audit pattern

See convention above. No script.

### Q2 — Synthetic fixture replacement

**Capture during the next `make test-all` invocation.** Synthetic fixtures match what you *think* the SDK returns; real ones catch the field you forgot existed. Two options, either is fine:

- Add a one-off block to the next test run that dumps the response to disk, copy it to `tests/fixtures/`.
- Write `scripts/capture_response_fixture.py` for deliberate captures.

**Do it in the next PR that touches the wrapper.** Don't let it slide to "later."

### Q3 — Retune trigger for `DEFAULT_THINKING_BUDGET`

Three concrete signals, whichever fires first:

1. **50 rows in `llm_runs` for any single task name.** Enough samples to compute output-token distributions and see whether the budget is over- or under-sized for that task.
2. **First instance of a `judge()` call hitting its budget limit** (thinking stops mid-stream). That's a hard signal the budget is too small. Wire a warning that logs when output approaches the budget — e.g. > 90% of budget consumed.
3. **Stage 5 (`synthesize`) has fired at least 10 times.** This is the most important and most likely-to-be-mistuned budget. Ten real synthesis runs give enough to eyeball whether 16k is right.

When any trigger fires, **open an orchestrator request** with the relevant `llm_runs` aggregates. Don't tune silently.

### Q4 — Other memory-sourced constants to audit now

Four were named. My reads:

| Constant | Action |
|---|---|
| Model names (`claude-opus-4-7`, `claude-haiku-4-5`) | Already verified — just annotate per the new convention. |
| Retry / timeout numbers | These came from feedback 003 (my memory, not Anthropic's documented defaults). Five-minute sanity check against SDK conventions and community practice. Annotate with the source decision. |
| TTL=5 min for ephemeral cache | You confirmed against the pricing page. Annotate. |
| `JUDGMENT_TASKS` set membership | Worth a manual review: any future Stage 1 sub-task that's about *picking which signals to keep* (not just dedup) is judgment. Any future Stage 4 sub-step that ranks candidates is judgment. Don't let the set drift into "things I added LLM calls for so far" — keep it semantically anchored to §3 of the brief. |

No deeper audit beyond annotations and the `JUDGMENT_TASKS` semantic review.

### Q5 — 1-hour cache TTL for task 010

**Likely needed.** Stage 1 clustering reuses the same long system prompt across many calls within a batch — hours, not minutes. 5-minute TTL means cache misses on the second hour, defeating most of the savings.

**Don't extend `_build_system` preemptively** (YAGNI), but when task 010 design happens, add an explicit note in the task's "Notes" section: *"if batch duration > 5 min, extend `_build_system` to accept `ttl='1h'` and use it for the shared system prompt."*

If Stage 1 batches end up being short bursts (each <5 min), this becomes moot — but plan as if you'll need it.

### Q6 — Carryover items from request 004

Acknowledged. `cache_blocks` limitations documented in `_build_system` docstring, `make test-all` cost documented in CLAUDE.md, 1h cache flagged via Q5 above. Nothing further to do.

## Action items summary

Before or during task 005, in any order:

1. Add the verify-constants convention to `CLAUDE.md` → Conventions.
2. Annotate the four named constants per Q4.
3. Capture a real Opus 4.7 response fixture during next `make test-all`; replace the synthetic one.
4. Wire a warning when `judge()` output approaches its thinking budget (>90%).
5. Plan for `ttl="1h"` cache extension in task 010's notes (don't implement yet).
6. Apply the verify-constants convention to Reddit constants in task 005 (rate limits, UA strings, etc.).

## Meta note

This request file maintained the same high quality as request 004 — specific, structured, with explicit "what would I do without intervention" and concrete questions. The reflexive self-check on the 3× miss ("strict reading vs pragmatic reading") was particularly valuable. Keep this format.

## Next step

Proceed to task 005 (Reddit ingester). The verify-constants convention applies — Reddit's documented rate limits, UA requirements, any subreddit-specific quirks, and any value reached for from memory gets a `# verified` annotation or a TODO. Pattern matters more than wrapper cleanup.
