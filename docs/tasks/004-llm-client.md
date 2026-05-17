# 004 — LLM client wrapper

**Goal:** Single Anthropic entrypoint that enforces the model policy, supports prompt caching, and logs every call to `llm_runs`.

**Complexity:** M

Depends on: 003.

## Deliverables
- Dep: `anthropic`.
- `apfun/llm/client.py`:
  - `class LLMClient` wrapping `anthropic.Anthropic` (sync).
  - Method `judge(task: str, system, messages, *, cache_blocks=None, thinking="high")` → uses `claude-opus-4-7` with extended thinking.
  - Method `mechanic(task: str, system, messages)` → uses `claude-haiku-4-5`.
  - Refuse a call to `mechanic` for a task in a `JUDGMENT_TASKS` set (`cluster`, `score`, `synthesize`, `prd`, `architecture`) — raise `PolicyViolation`.
  - Log to `llm_runs`: model, task, token counts (input, output, cache read/write), latency, ok/error.
  - Cost estimate using a static price table; record the exact prices it used in the row (denormalized so future price changes don't break history).
- `apfun/llm/prompts/` — directory for prompt templates (Jinja `.j2`), starts empty except a `_shared.j2` partial for the apfun system preamble.
- Helper `chunked_cache(blocks)` to mark long context (review corpora etc.) as `cache_control: {"type": "ephemeral"}`.

## Acceptance
- Unit test (with `respx` or a mock transport) verifies:
  - `judge("cluster", ...)` issues a request with `model=claude-opus-4-7` and `thinking={"type": "enabled", "budget_tokens": ...}`.
  - `mechanic("dedup", ...)` issues a request with `model=claude-haiku-4-5`.
  - `mechanic("cluster", ...)` raises `PolicyViolation` without making any network call.
  - A row is written to `llm_runs` on success and on failure.
- No call sites of `anthropic.Anthropic` exist outside `apfun/llm/`. Add a `ruff` rule or a pyright-friendly re-export to make this enforceable.
