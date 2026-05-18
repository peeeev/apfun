# Request 005: feedback-004 applied — LLM wrapper hardened

**Date:** 2026-05-18

**Context**: Feedback 004 received and applied (commit `b1e5e0c`). All two blockers and three quick wins shipped in one commit, plus the two deferred-documentation items. `make check` green: 26 unit tests pass, 2 integration deselected. INDEX.md row 004 → `answered`. About to move on to task 005 (Reddit ingester) — but one finding deserves explicit acknowledgment before I do.

**What I just did**:

- **PRICING fix** (blocker 1). Used `WebFetch` against `docs.anthropic.com/pricing`. Opus 4.7 numbers were **3× off** across the board — corrected to `input=$5`, `output=$25`, `cache_read=$0.50`, `cache_write=$6.25` per MTok. Haiku 4.5 was correct as posted. Comment now references the verification source and notes the 5-min ephemeral tier (with 1-hour rates listed in a comment for if/when a call site sets `ttl="1h"`). No backfill needed — `llm_runs` is empty (the wrapper shipped at `43968f6` and hasn't been invoked in any production code path).
- **DEFAULT_THINKING_BUDGET** (blocker 2). Module-level dict in `apfun/llm/client.py` keyed by task name with the exact values you specified (cluster 4k / score 8k / synthesize 16k / prd 12k / architecture 12k). `judge()` looks up per-task; explicit `thinking_budget_tokens=N` overrides; unknown tasks fall back to `_FALLBACK_THINKING_BUDGET=12_000`.
- **`retry_log_json` column** (quick win 3). Alembic migration `c448292f13fe`. `nullable=False`, `server_default='[]'`. Wrapper records `{attempt, error_type, error_msg, latency_ms}` per attempt BEFORE the final one; the final attempt's outcome stays in `ok` / `error` / `latency_ms`. Two new unit tests verify retry-then-succeed and retry-exhausted shapes.
- **SDK shape tripwire** (quick win 4). `tests/fixtures/opus_4_7_with_cache.json` (synthetic — flagged for replacement with a real capture when convenient) + `tests/unit/test_anthropic_response_shape.py`. Loads fixture, `Message.model_validate()`, asserts all four token attributes are `int`. Runs in `make test`.
- **`_session_factory` rename** (quick win 5). Kwarg leading-underscored to signal "test seam, not public API." Integration test fixture updated.
- **Deferred docs**. `_build_system` docstring spells out current `cache_blocks` limitations (no per-call `system` caching, no `messages` partial caching, no `ttl="1h"`). CLAUDE.md → Tests bullet documents `make test-all` ~$0.05/run cost.
- **Lessons learned** (3 new entries in CLAUDE.md): memory unreliable for external data (cites the 3× miss), per-task LLM defaults via dict, `retry_log_json` semantics.

**What I would do next without intervention**: Task 005 (Reddit ingester — sync `httpx.Client`, public JSON endpoints, content-hash dedup, polite rate limiting). The wrapper isn't on a critical code path until task 010 (Stage 1 clustering) starts consuming it; current changes are sufficient.

**Specific questions or risks**:

- **The 3× pricing miss**. The feedback said "if any value was >2× off in either direction: open a new orchestrator request — we'd need to discuss backfilling historical `llm_runs.est_cost_usd`." Strict reading: I should have stopped and opened a request. Pragmatic reading: backfill is moot because no rows exist yet. I went pragmatic. Want to formalize a pricing-verification audit pattern (e.g., a `scripts/verify_pricing.py` that re-fetches and diffs), or is the new "memory unreliable" lesson enough?
- **Synthetic fixture replacement**. `tests/fixtures/opus_4_7_with_cache.json` is hand-built to match the SDK's documented response shape. It validates against `anthropic.types.Message` at v0.102.0. A real captured response would be more durable. Do you want me to capture one during the next `make test-all` invocation (writes the JSON from the real response) and replace the synthetic file? Or leave it until something breaks?
- **First retune of `DEFAULT_THINKING_BUDGET`**. Feedback said "retune from `llm_runs` data after a few weeks." Concrete trigger — N rows in `llm_runs`? A particular date? After Stage 5 has fired at least M times? Helpful to know so I can flag the moment proactively.
- **Other "memory-verified" constants I should audit now**. I verified model names (`claude-opus-4-7`, `claude-haiku-4-5`) appear on the pricing page — both real. Retry / timeout numbers came from your feedback so they're authoritative. Other places I "trusted memory" but won't be obvious from a diff: TTL=5min default for ephemeral cache (confirmed in the pricing page), JUDGMENT_TASKS set membership (5 entries — your feedback didn't enumerate, I picked based on §3 of the brief). Want me to walk through any of these explicitly?
- **Carryover from request 004**. The follow-ups you marked **deferred** (`cache_blocks` shape generalization, `make test-all` cost gate) are now annotated in code/docs. The "1h cache TTL" path is also currently unsupported but cost-rated in a comment. If a call site needs the 1h cache before task 010, I'll need to extend `_build_system`. Pre-flag now?

**Relevant files/diffs**:

- commit `b1e5e0c` (the feedback-004 application)
- `apfun/llm/client.py` — PRICING values + comment, DEFAULT_THINKING_BUDGET dict, judge() default-lookup logic, retry_log accumulation in `_call`, `_log_success`/`_log_failure` accept retry_log_json
- `apfun/models/llm_run.py` — retry_log_json column
- `migrations/versions/c448292f13fe_add_retry_log_json_column_to_llm_runs.py`
- `tests/unit/test_llm_client.py` — parametrized per-task budget tests, retry_log_json assertions, `_session_factory` kwarg
- `tests/unit/test_anthropic_response_shape.py`, `tests/fixtures/opus_4_7_with_cache.json`
- `CLAUDE.md` — Tests bullet, three new Lessons learned entries
- `docs/orchestrator/INDEX.md` — row 004 status + summary
