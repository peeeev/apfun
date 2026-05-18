# Request 006: feedback-005 applied — verify-constants + budget warning

**Date:** 2026-05-18

**Context**: Feedback 005 received and fully applied at commit `aae8f1b`. New project convention in place ("Verify external constants inline"); the four named constants in `apfun/llm/client.py` are annotated; the >90%-of-thinking-budget warning is wired and tested; `scripts/capture_response_fixture.py` is ready to run; task 010 carries the 1h-cache-TTL pre-flag. `make check` green, 29 unit tests pass. Ready for task 005 (Reddit ingester) — which is where the verify-constants convention will get its first real exercise on a non-LLM external surface.

**What I just did**:

- **CLAUDE.md → Project conventions**: new bullet "Verify external constants inline." Every numeric/string constant sourced externally carries `# verified YYYY-MM-DD <source>` immediately above. No standalone `verify_*.py` script — the convention is the audit. Three new Lessons-learned entries: convention pointer, DEFAULT_THINKING_BUDGET retune triggers (50 rows / 90% warning / 10 synthesize calls), JUDGMENT_TASKS semantic anchor.
- **`apfun/llm/client.py` — four constants annotated**:
  - `JUDGE_MODEL` / `MECHANIC_MODEL` → verified against the pricing page (both model IDs appear there).
  - `_MAX_RETRIES` / `_JUDGE_TIMEOUT_S` / `_MECHANIC_TIMEOUT_S` → verified pointer to `docs/orchestrator/003-feedback.md` (the decision source).
  - `_EPHEMERAL_CACHE_CONTROL` — new module-level constant hoisting the `{"type": "ephemeral"}` marker so the verified annotation has a home; used inside `_build_system`.
  - `JUDGMENT_TASKS` — comment block now states the semantic anchor (brief §3) and explicitly warns against drift.
- **`apfun/llm/client.py` — budget warning**: `_maybe_warn_budget(task, thinking, output_tokens)` fires `logger.warning(...)` when `output_tokens >= 0.9 * thinking_budget_tokens` on a `judge()` call. Three new unit tests cover (a) ≥90% fires, (b) below threshold silent, (c) `mechanic()` never warns. Heuristic uses `output_tokens` (thinking + text combined) as a proxy — SDK 0.102.0 doesn't surface thinking-only tokens.
- **`scripts/capture_response_fixture.py`**: two-call flow (first writes cache, second reads it) → dumps the real response to `tests/fixtures/opus_4_7_with_cache.json`. Run once with `APFUN_ANTHROPIC_API_KEY` set; costs a few cents. The current fixture is the synthetic hand-built version; the script lets you replace it on the next `make test-all` cycle without any one-off code.
- **`docs/tasks/010-stage1-clustering.md` → Notes**: explicit pre-flag — if a clustering batch exceeds 5 min wall-clock, extend `_build_system` for `ttl="1h"` and add `cache_write_1h` to PRICING (Opus 4.7 1h rate is $10/MTok; already noted in the PRICING comment).
- **INDEX.md**: row 005 → `answered` with summary.

**What I would do next without intervention**: Task 005 (Reddit ingester) — sync `httpx.Client`, public JSON endpoints, content-hash dedup, polite rate limiting. The verify-constants convention will land its first non-LLM exercise: Reddit's documented rate limits, UA-string requirements, any subreddit-specific endpoint quirks all get inline `# verified` annotations.

**Specific questions or risks**:

- **Budget-warning heuristic accuracy.** Using `output_tokens` as the proxy is conservative — fires when thinking maxes out AND when total output was just very large. If the SDK starts surfacing `thinking_tokens` separately in a future version, the warning should switch to that signal for precision. Worth wiring a TODO in the code, or rely on the SDK-shape tripwire test catching it?
- **Visibility of the unfilled capture-fixture script.** `scripts/capture_response_fixture.py` is ready, but the synthetic fixture is still in place. Without a TODO somewhere I'll check regularly, this might slide. Options: (a) add a banner comment to the fixture JSON itself saying "synthetic — replace via scripts/capture_response_fixture.py", (b) add a pytest skip warning that fires when the fixture matches the synthetic content, (c) trust the convention. Leaning (a) as the lightest.
- **Retune-trigger instrumentation.** The three retune triggers (50 rows / 90% warning / 10 synthesize) are documented in CLAUDE.md and inline. Concrete check would be a small `scripts/check_retune_triggers.py` that prints "fire X is hit" — but that's exactly the kind of standalone audit script the convention rejects. Alternative: bake the check into a sources-health UI route once task 022 builds the admin UI dashboards. Adequate to wait?
- **`JUDGMENT_TASKS` semantic completeness.** Current 5 entries cover Stage 1/4/5/Gate-1/Gate-2 — matches §3 exactly. Brief §3 also mentions niche evaluation, competitor analysis, prioritization, "is this opportunity real" — these don't have dedicated wrapper call sites yet. When task 016 (Stage 3 competitor scraping) lands its `mechanic("extract_pricing")` + `judge("review_pricing")` calls (per the task spec), `review_pricing` would belong in `JUDGMENT_TASKS`. Pre-add now, or add as the call sites land?
- **Convention propagation.** Task 005 onward gets the verify-constants treatment from line 1. But existing files outside `apfun/llm/client.py` (e.g., `apfun/config.py`'s host/port defaults — internal, not external; `apfun/models/base.py`'s `_utcnow` — pure code, no external source) don't carry annotations. I read the convention as "external constants only" — `host="0.0.0.0"` is a project decision, not an external published value. Confirm that read?

**Relevant files/diffs**:

- commit `aae8f1b` (the feedback-005 application)
- `CLAUDE.md` — new convention bullet + three Lessons entries
- `apfun/llm/client.py` — annotations + `_maybe_warn_budget` + `_EPHEMERAL_CACHE_CONTROL`
- `scripts/capture_response_fixture.py`
- `tests/unit/test_llm_client.py` — three new caplog tests
- `docs/tasks/010-stage1-clustering.md` — Notes addition
- `docs/orchestrator/INDEX.md`
