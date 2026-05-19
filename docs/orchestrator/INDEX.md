# Orchestrator request index

See `CLAUDE.md` → "Orchestrator Pattern (External Review)" for the workflow.

| NNN | YYYY-MM-DD | topic-slug                | status   | one-line decision summary                                            |
|-----|------------|---------------------------|----------|----------------------------------------------------------------------|
| 001 | 2026-05-17 | gate2-plan-and-stack      | answered | sync DB, decision/pipeline_stage split, Resend, Complexity tags      |
| 002 | 2026-05-18 | db-foundations-checks     | answered | pragmas via connect listener (+foreign_keys=ON), both invocation forms boot identically, Makefile |
| 003 | 2026-05-18 | llm-client-plan           | answered | judge/mechanic only, every call logs to llm_runs, pricing dict with verified date, real extended thinking, retries=3, integration tests |
| 004 | 2026-05-18 | llm-wrapper-results       | answered | fix PRICING (was 3× off for Opus), per-task DEFAULT_THINKING_BUDGET, retry_log_json column, SDK-shape tripwire test, rename `_session_factory`, docstring + CLAUDE.md notes (no cache_blocks generalization, no test-all gate) |
| 005 | 2026-05-18 | llm-wrapper-hardened      | answered | new "Verify external constants inline" convention, annotate 4 named constants, budget >90% warning + tests, capture_response_fixture.py script, 1h-cache plan flagged in task 010, retune triggers documented |
| 006 | 2026-05-18 | verify-constants-applied  | answered | TODO in `_maybe_warn_budget`, `_meta_note` in synthetic fixture, no-preemptive-extension comment on `JUDGMENT_TASKS`, tighten convention text (external-only test), cache-hit assertion in capture script, budget health panel noted in task **021** (feedback referenced 022; admin observability lives in 021) |
| 007 | 2026-05-18 | pre-task-005-tidy         | answered | strip `_meta_note` in tripwire test, add failing `test_fixture_is_real_capture` (no xfail), INDEX row 006 spells out 021-vs-022 redirect; tactical Reddit guidance baked into task 005 plan |
| 008 | 2026-05-18 | synthetic-fixture-guard   | answered | proceed against red gate (option b), Reddit username must fail-loud at startup, per-source rate-limit buckets (source-agnostic `TokenBucket` in `apfun/sourcing/_rate_limit.py`), new `# heuristic YYYY-MM-DD — <rationale>` annotation form for judgment-derived constants; CLAUDE.md convention + Tests workflow updated |
| 009 | 2026-05-18 | pre-task-005-ready        | answered | open `docs/tasks/023-github-actions-ci.md` (CI sequenced between Phase E and F), new "Contract tests for external schemas" convention (sibling to verify-constants), bake `sources.consecutive_failures` + three-strikes auto-disable into task 005, capture-but-tag deleted Reddit content |
| 010 | 2026-05-18 | task-005-spec-final       | answered | fixture refresh opportunistic (with `_fixture_meta` header), `TERMINAL_STATUSES={403,404,410}` + >50%-batch UA-block heuristic, CI `contents: read` only with rationale in task 023 Notes, `# TODO verify by end of task <NNN>` discipline added to CLAUDE.md |
