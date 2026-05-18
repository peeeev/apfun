# Feedback 006 — verify-constants tightening + retune trigger placement

**Date:** 2026-05-18
**Request:** 006-verify-constants-applied.md
**Outcome:** Approved. Five calibrations to the work done, plus one small addition to the capture-fixture script. No blockers for task 005.

## Calibrations to the just-applied work

### 1. Budget-warning heuristic — add TODO, keep current logic

The conservative `output_tokens` proxy is fine for now. False positives are cheap; false negatives (thinking silently maxed out) are what we're guarding against, and the current heuristic catches them.

Add a TODO next to `_maybe_warn_budget`:

```python
# TODO: when SDK exposes thinking_tokens separately (post v0.102.0),
# switch this signal from output_tokens to thinking_tokens for precision.
```

The SDK-shape tripwire test only fires on breakage, not on capability additions. A TODO surfaces during any future read of the function — that's the right reminder cadence.

### 2. Synthetic fixture visibility — option (a), banner in the JSON

Add a `_meta_note` field to the synthetic fixture JSON:

```json
{
  "_meta_note": "SYNTHETIC FIXTURE — replace with real capture via scripts/capture_response_fixture.py on the next make test-all run",
  ...
}
```

Cheapest, can't fail, immediately visible to anyone opening the file.

**Don't** go with the pytest warning option — warnings that fire every test run become noise you stop reading.

### 3. Retune-trigger instrumentation — defer to task 022 admin UI

Don't write a standalone `check_retune_triggers.py` — that would violate the no-standalone-audit-scripts principle.

Instead: when task 022 builds the sources-health admin UI, add a small "LLM budget health" panel that reads `llm_runs` aggregates and shows whether any retune trigger has fired (50 rows for any task, 90% warnings logged, 10 synthesize calls). The data isn't urgent — these are *eventual* tuning signals, not real-time alerts. The 90% warning fires in logs in real time as the highest-signal trigger; the slower triggers can wait for the panel.

Note this in `docs/tasks/022-...` Notes section so it doesn't get forgotten.

### 4. `JUDGMENT_TASKS` membership — extend as call sites land, not preemptively

Don't pre-add `review_pricing` or other entries that don't yet have call sites. The set's meaning depends on the code actually using it. Adding speculative entries creates drift from reality.

Discipline: when task 016 introduces a `judge("review_pricing")` call, the PR that adds the call site also adds `review_pricing` to `JUDGMENT_TASKS` in the same diff. That's the only moment the membership is reviewable.

Add a comment above the set:

```python
# JUDGMENT_TASKS — extend in the same PR that introduces the call site,
# never preemptively. Each entry must correspond to an actual judge() callsite.
```

### 5. Convention scope — external only (your read is correct)

The verify-constants convention applies to constants sourced from outside the repository: pricing pages, API specifications, rate-limit documentation, third-party-published values. It does **not** apply to internal project decisions (`host="0.0.0.0"`, status enum values, computed bounds, fallback defaults).

Tighten the CLAUDE.md convention text to make this explicit:

> **External constants only.** A constant is "external" if its value originated from a third-party document, API specification, pricing page, or other source outside this repository. Internal project decisions (defaults you chose, enum values, computed bounds) don't require annotation — their authority is the codebase. When in doubt, ask: *"if a teammate questioned this number, would I cite an external URL or a project decision?"*

## One unprompted addition — capture-fixture script verification

Glance at `scripts/capture_response_fixture.py`. The two-call flow assumes the second call hits cache; if Anthropic's cache is having a bad minute, the captured fixture might lack `cache_read_input_tokens > 0`, defeating its purpose.

Add an assertion before saving:

```python
assert response.usage.cache_read_input_tokens > 0, (
    "second call did not hit cache — fixture would be misleading. Retry."
)
```

If it fails, the script exits non-zero and you re-run. Big payoff in fixture quality for one extra line.

## Action items

For task 005's PR (or a small prep commit before it):

1. Add the TODO to `_maybe_warn_budget`.
2. Add the `_meta_note` to the synthetic fixture JSON.
3. Add the no-preemptive-extension comment above `JUDGMENT_TASKS`.
4. Tighten the verify-constants convention text in CLAUDE.md per item 5 above.
5. Add the cache-hit assertion to `scripts/capture_response_fixture.py`.
6. Note the LLM budget health panel in `docs/tasks/022-...` Notes.

These are all small enough to land in a single prep commit. None block starting task 005 itself.

## Next step

Task 005 — Reddit ingester. First real non-LLM exercise of the verify-constants convention.

External constants to expect:

- Documented rate limits (Reddit's published quota — recently changed, verify against current docs)
- User-Agent string requirements (Reddit's UA convention; non-conformant UAs get blocked)
- JSON endpoint pagination shape (25-item default, `after` cursor format)
- Per-subreddit listing endpoints behavior
- Any subreddit you depend on having `is_active=True` configuration — but that's a project decision, not external

Inline `# verified YYYY-MM-DD <url>` annotations or `# TODO verify` for anything that needs follow-up.

## Meta note

Q4 and Q5 in the request were particularly well-formed — they identified the *principle* underlying the question, not just the specific instance. That's the discipline that makes orchestrator requests valuable. Keep this shape.
