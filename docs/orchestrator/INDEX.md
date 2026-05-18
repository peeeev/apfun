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
| 008 | 2026-05-18 | synthetic-fixture-guard   | open     | ---                                                                                                                                                                                                                          |
