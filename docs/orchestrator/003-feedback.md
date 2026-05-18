# Feedback 003 — Task 004 LLM wrapper design

**Date:** 2026-05-17
**Request:** 003-task004-llm-wrapper-design.md (issued at task 003 PR review, since task 004 design choices affect everything downstream)
**Outcome:** Approved with seven required design constraints before writing the wrapper.

## Why this gets a heavyweight review

Task 004 is the load-bearing piece for the model selection policy from `project-brief.md` §3. Every LLM call in the rest of the project flows through this wrapper. Designing it well once and never relitigating is cheap; retrofitting these constraints later means touching every call site. Spend the up-front thinking here.

## Required design constraints

### 1. Two explicit entrypoints — no raw model selection

Expose only:

- `judge(prompt, ...)` → Claude Opus 4.7 with extended thinking
- `mechanic(prompt, ...)` → Claude Haiku 4.5

The model name is bound inside the wrapper. Callers cannot pass an arbitrary `model=` parameter. If a third option becomes genuinely necessary, it gets added to `client.py` in a dedicated PR — that PR is where the §3 policy can be re-litigated, not at every call site.

This is how the model policy holds across hundreds of call sites and across future Claude Code sessions that won't have read the brief carefully.

### 2. Every call writes to `llm_runs` from inside the wrapper

Before returning to the caller, record:

- `task` (string, caller-provided, e.g. `"stage1.cluster"`, `"stage5.synthesize"`)
- `model` (from the entrypoint, not parameterizable)
- `input_tokens`, `output_tokens`
- `cache_read_tokens`, `cache_write_tokens`
- `latency_ms`
- `est_cost_usd` (computed at call time, persisted as dollars)
- `ok` (boolean)
- `error` (nullable string)
- `candidate_id` (optional FK parameter — for joining cost back to candidates later)

**Non-negotiable for v1.** If task 004 ships without `llm_runs` writes, we'll have weeks of LLM activity untracked and no answer to "where did the money go."

### 3. Pricing as a small lookup table in `client.py`

```python
PRICING = {
    "claude-opus-4-7":            {"input_per_mtok": ..., "output_per_mtok": ...},
    "claude-haiku-4-5-20251001":  {"input_per_mtok": ..., "output_per_mtok": ...},
}
# verified 2026-05-17
```

Persist computed `est_cost_usd` per row, not a formula. When prices change, update the constant for new rows only; historical rows keep their original cost calculation. This is what the brief means by "denormalize pricing assumptions in the row so future price changes don't break historical cost queries."

### 4. Prompt caching enabled from day one

Anthropic's prompt caching cuts cost dramatically on:

- Stage 1 clustering (same system prompt, hundreds of dedup calls per day)
- Stage 5 synthesis (same instructions, varying inputs)

Wire `cache_control: {"type": "ephemeral"}` markers on reusable system prompts now. The schema already has `cache_read_tokens` / `cache_write_tokens` columns from task 003, so the persistence side is ready.

### 5. Explicit retries and timeouts

- `max_retries=3` (SDK default is fine, but be explicit so it's visible)
- `timeout=30` (seconds) for `mechanic`
- `timeout=120` (seconds) for `judge` — extended thinking takes longer

Log retries somehow — either single row in `llm_runs` with an `attempt` counter column, or N rows linked by a parent_id. Either is fine; just be consistent and document the choice in CLAUDE.md.

### 6. Extended thinking actually enabled for `judge`

The brief says "Opus 4.7 with extended thinking (xhigh)." This is a real API parameter, not a vibe:

```python
thinking={"type": "enabled", "budget_tokens": 12000}  # ~xhigh effort
```

Easy to forget. The "xhigh" intent is meaningless if the parameter isn't actually set. **Verify with a test that asserts the response contains a `thinking` block.**

### 7. Integration tests, gated separately from unit tests

At least one real-API integration test per entrypoint:

- `tests/integration/test_judge_real.py` — one real `judge` call
- `tests/integration/test_mechanic_real.py` — one real `mechanic` call

Marked with a pytest marker (e.g. `@pytest.mark.integration`) and skipped by default. `make test` runs unit only; `make test-all` runs both.

**Reason:** mocking the Anthropic SDK is easy and will pass green forever even if the real API request structure changes or the wrapper hands the SDK the wrong shape. One real call per entrypoint, gated behind opt-in, catches integration breakage at the cost of a few cents per `make test-all` run.

## Aside

Future tasks (005 onward — sourcing) will start consuming the wrapper. If task 004 ships cleanly with all seven constraints, the rest of the project gets LLM-call discipline for free. If any constraint slides into "I'll do it later," it almost certainly won't get done.

## Next step

Implement task 004 with all seven constraints. Open the PR. Include the integration tests in the same PR but with the marker so they don't run unless `make test-all`.
