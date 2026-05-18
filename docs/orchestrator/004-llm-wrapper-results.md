# Request 004: task 004 LLM wrapper — results and follow-ups

**Date:** 2026-05-18

**Context**: Task 004 landed at commit `43968f6` with all seven feedback-003 constraints applied. About to move to task 005 (Reddit ingester) — the wrapper itself isn't on the critical path until task 010 (Stage 1 clustering) starts consuming it, so this is the right moment to flag the choices I made that you might want to redirect before they get cemented across hundreds of call sites.

**What I just did**:

- Two entrypoints, no model parameter. `LLMClient.judge()` → `claude-opus-4-7` with `thinking={"type":"enabled","budget_tokens":12_000}` default (range 8k–16k via `thinking_budget_tokens` kwarg). `LLMClient.mechanic()` → `claude-haiku-4-5`, no thinking. `JUDGMENT_TASKS = {"cluster", "score", "synthesize", "prd", "architecture"}` blocks `mechanic()` for those tasks with `PolicyViolation`.
- Every call writes one row to `llm_runs` from inside the wrapper: model, task, all four token counts, latency, est_cost_usd, attempts, candidate_id, ok, error. Added `attempts` column via migration `c9654c48dc33`.
- Retries: wrapper owns the loop (SDK `max_retries=0`), exponential backoff with jitter, per-call timeouts. Single `llm_runs` row per logical call with `attempts` reflecting the final count.
- Pricing: module-level `PRICING` dict with `# verified 2026-05-18`; cost computed at call time; persisted dollar value (not formula).
- Prompt caching: `cache_blocks=[...]` mapped to system content blocks with `cache_control: {"type":"ephemeral"}`; per-call instructions appended uncached.
- Tests: 9 unit (mocked SDK) + 2 integration (`@pytest.mark.integration`, skipped by default, gated on `APFUN_ANTHROPIC_API_KEY`). `make test` runs unit only; `make test-all` runs both.

**What I would do next without intervention**: Task 005 (Reddit ingester — doesn't itself call the LLM). Then 006-009 (other ingesters). The wrapper gets stressed for real at task 010. I would not re-open the wrapper to harden it until a real call site exposes a gap.

**Specific questions or risks**:

- **PRICING numbers** are best-guess values I picked from memory ($15/$75/$1.50/$18.75 per 1M for Opus 4.7 input/output/cache-read/cache-write; $1/$5/$0.10/$1.25 for Haiku 4.5). Should be verified against Anthropic's published rates before any real load — and the `# verified` comment bumped to the date the verification happened, not today's placeholder.
- **Retry observability**: the row records `attempts` (the final count) and `error` (the last exception's text on failure). Mid-flight visibility is zero — if attempt 2 of 3 succeeds, we see `attempts=2` but not which exception fired on attempt 1. Worth a separate `retry_log_json` column or a sibling `llm_retries` table, or accept the looseness?
- **`cache_blocks` shape** is a list of strings — clean for "long preamble + small per-call instructions" but doesn't let callers cache the middle of a system prompt or cache part of the per-call instructions. Generalize now or wait for a real call site that needs more control?
- **Default thinking budget** is a flat 12000 for every `judge()` call regardless of task. Stage 1 dedup-pass clustering probably never needs that. Worth per-task defaults baked into a small lookup, or rely on call-site overrides via `thinking_budget_tokens=N`?
- **Pyright + SDK typing**: forced a `cast(Any, msg.usage)` because the anthropic 0.102.0 stubs don't resolve attributes cleanly through `messages.create(**kwargs)`. Risk: a future SDK rename of `cache_creation_input_tokens` or `cache_read_input_tokens` silently logs zeros. Worth a fixture-recorded real-API response test that asserts both keys are still present, or accept that we'll notice via the integration test?
- **`make test-all` cost discipline**: each run hits the real API and costs cents. No rate limit on local invocations. Trust the discipline, or gate behind a confirmation prompt / env var?
- **`session_factory` kwarg** on `LLMClient.__init__` is there for tests; production should use the default. Leaving it as a regular kwarg vs. renaming to `_session_factory` (signaling "private use"). Either way works; flag if you have a preference.

**Relevant files/diffs**:

- `apfun/llm/client.py` (~280 lines, single file)
- `apfun/llm/__init__.py`, `apfun/llm/prompts/_shared.j2` (placeholder)
- `apfun/models/llm_run.py` — added `attempts` column
- `migrations/versions/c9654c48dc33_add_attempts_column_to_llm_runs.py`
- `tests/unit/test_llm_client.py` (9 tests)
- `tests/integration/test_llm_client_integration.py` (2 tests)
- commit `43968f6`
