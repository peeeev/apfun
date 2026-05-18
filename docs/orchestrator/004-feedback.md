# Feedback 004 — LLM wrapper post-build review

**Date:** 2026-05-18
**Request:** 004-task004-llm-wrapper-results.md
**Outcome:** Approved overall — wrapper landed clean. Two blockers before task 010 (Stage 1 clustering) starts consuming it. Three quick wins to bake in now. Two deferred items.

## Blockers before task 010

These must be resolved before any code path actually starts calling `judge()` or `mechanic()` in anger.

### 1. Verify PRICING numbers against published rates

The current values were filled in from memory. Cost calculations across hundreds of `llm_runs` rows will be wrong if the constants are wrong, and wrong cost data is worse than no cost data (it lies confidently).

Action:

- Fetch current pricing from `docs.anthropic.com` for Opus 4.7 and Haiku 4.5 (input, output, cache-read, cache-write per 1M tokens).
- Update `PRICING` dict in `apfun/llm/client.py`.
- Bump `# verified YYYY-MM-DD` to the date verification actually happened.
- **If any value was >2x off** in either direction: open a new orchestrator request — we'd need to discuss backfilling historical `llm_runs.est_cost_usd` for whatever rows exist by then.

### 2. Per-task default thinking budgets

Flat 12000 for every `judge()` call wastes Opus capability on trivial decisions and under-thinks the important ones. The whole point of the two-tier policy is to spend reasoning where it pays off.

Add to `apfun/llm/client.py`:

```python
DEFAULT_THINKING_BUDGET: dict[str, int] = {
    "cluster":      4_000,    # Stage 1 dedup — narrow choices
    "score":        8_000,    # Stage 4 quantitative weighing
    "synthesize":  16_000,    # Stage 5 differentiation — the most important
    "prd":         12_000,    # PRD generation for approved opportunities
    "architecture":12_000,    # Gate 2 tech-stack proposals
}
```

`judge(task=...)` looks up the budget from this dict; explicit `thinking_budget_tokens=N` still overrides. These are starting numbers — retune from `llm_runs` data after a few weeks.

The `synthesize` budget is the one to guard most jealously. That's where opportunity quality lives.

## Quick wins

Same PR as the blockers, or next minor commit. Cheap now, expensive to retrofit.

### 3. `retry_log_json` column on `llm_runs`

The current shape records `attempts` (final count) and `error` (last exception text). Mid-flight visibility is zero. When something flaps in production — rate limit on attempt 1 succeeding on attempt 2, transient 500s, etc. — we'll want to know what actually happened without grepping container logs.

Action:

- Alembic migration adds `retry_log_json` to `llm_runs` (nullable, defaults to empty list).
- Wrapper populates per-attempt records: `[{attempt: 1, error_type: "RateLimitError", error_msg: "...", latency_ms: 320}, ...]`.
- Final attempt's outcome stays in the top-level `ok`/`error`/`latency_ms` columns.
- Sibling table would be over-normalized for this low-volume data. JSON column is correct.

### 4. SDK shape tripwire test

The `cast(Any, msg.usage)` workaround means a future anthropic SDK rename of `cache_creation_input_tokens` or `cache_read_input_tokens` would silently log zeros instead of failing loudly.

Action:

- Capture one real Opus 4.7 response with cache hits as a fixture (`tests/fixtures/opus_4_7_with_cache.json`).
- Add `tests/unit/test_anthropic_response_shape.py`: load the fixture, assert all four token-counting attributes (`input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`) are present with int values.
- Runs in `make test` (unit). SDK upgrades that break token counting fail CI immediately.

### 5. Rename `session_factory` → `_session_factory`

Convention nudge — signals "test seam, not public API." Two-character change, zero behavior impact, easier to do now than after a half-dozen tests reach for the public name.

## Deferred — don't act on these

### 6. `cache_blocks` shape generalization

Current shape (list of strings → system content blocks) handles obvious cases. Generalizing without a concrete need is YAGNI. When task 010 (Stage 1 clustering) or task 019 (Stage 5 synthesis) shows up needing finer control, refactor then.

Action: one-line docstring note in `apfun/llm/client.py` about current limitations, so future-you doesn't waste time wondering if it already supports something it doesn't.

### 7. `make test-all` cost gate

You're the only user; "cost" is cents per invocation. A confirmation prompt creates friction every time you actually need to verify integration. If someone else ever runs this repo, then add a gate.

Action: document the ~$0.05/run cost in CLAUDE.md → Testing. No programmatic gate.

## Meta note

The request file (004) is exactly the right shape: specific questions, concrete options, no waffle. Keep this format for future orchestrator requests. The blockers/quick-wins/deferred structure of this feedback is worth preserving as a template for future feedback when there are many items to triage.

## Next step

Apply blockers 1–2 and quick wins 3–5 (same PR or two small ones). Then proceed to task 005 (Reddit ingester). Wrapper hardening above is independent of the ingester tasks (005–009), so order between them doesn't matter — do whichever feels cleaner.
