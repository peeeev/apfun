# Request 003: pre-task-004 LLM client plan

**Date:** 2026-05-18

**Context**: Task 003 landed in two commits per the previous gate's request to split — `b70e24a` (core pipeline: demand_checks, approvals, competitive_analyses, scores, opportunities, projects) and `39af30c` (telemetry: llm_runs, scheduler_runs, api_usage). Both migrations round-trip, every FK is explicitly indexed, JSON columns are reassign-only (documented), 11 unit tests green. About to start task 004 (LLM client wrapper).

**What I just did**: Built nine pipeline + telemetry models, hoisted `enum_values` and `check_enum_sql` helpers to `apfun/models/base.py`, refactored `Candidate` to use them, retroactively added `ix_candidate_signals_raw_signal_id` (the composite PK only covers the `candidate_id` left-prefix). CLAUDE.md grew two new convention bullets (JSON reassign-only, every FK indexed) and three Lessons-learned entries.

**What I would do next without intervention**: `apfun/llm/client.py` wrapping `anthropic.Anthropic` (sync). Class `LLMClient` with two methods — `judge(task, system, messages, *, cache_blocks=None, thinking="high")` for Opus 4.7 and `mechanic(task, system, messages)` for Haiku 4.5. A `JUDGMENT_TASKS` set so `mechanic("cluster", ...)` raises `PolicyViolation`. Every successful call writes an `llm_runs` row (model, task, token counts, latency, est_cost, ok). Failures log `ok=False` with the error.

**Specific questions or risks**:
- Pricing constants: bake as a module-level dict, or external file? How do you want stale-pricing audited — `# verified YYYY-MM-DD` comment, separate `pricing.py`?
- Retries / timeouts: the SDK has built-in retry but I haven't picked a max or distinguished judge from mechanic. Concrete numbers per call kind?
- Extended thinking: my draft has `thinking="high"` as a label, not a real budget. Wire `thinking={"type":"enabled","budget_tokens":N}` from day one, or defer until Stage 5?
- Test approach: unit tests with mocked SDK catch signature regressions but not real-API quirks (parameter format, response shape for thinking blocks). Worth at least one tiny integration test per method that costs a few cents but exercises the SDK?
- Error logging: separate row per attempt, or single row with an `attempt`/`error` column? SDK's retries are transparent — would need to choose.

**Relevant files/diffs**:
- commits `b70e24a` and `39af30c` (task 003)
- `docs/tasks/004-llm-client.md`
- `apfun/models/llm_run.py` (current shape)
