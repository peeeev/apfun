# Request 007: feedback-006 applied — six calibrations before task 005

**Date:** 2026-05-18

**Context**: Feedback 006 received and fully applied at commit `7e90c20`. All six calibrations landed in one prep commit. `make check` green, 29 unit tests pass, 2 integration deselected. Ready to start task 005 (Reddit ingester) — the first task that exercises the verify-constants convention on a non-LLM surface.

**What I just did**:

- **`_maybe_warn_budget` TODO**: comment above the comparison flags the switch from `output_tokens` (proxy) to `thinking_tokens` (precise) when a future SDK version exposes it. Verbatim wording from the feedback.
- **JUDGMENT_TASKS comment**: added the "extend in the same PR that adds the call site — never preemptively" line. Each entry must correspond to an actual `judge()` callsite somewhere in the repo.
- **Convention text tightened in CLAUDE.md**: "external constants only" is now spelled out — internal project decisions (host/port, enum values, computed bounds) explicitly don't need annotation. Added the "would I cite an external URL or a project decision?" heuristic so the rule is testable.
- **`_meta_note` banner in fixture JSON**: `tests/fixtures/opus_4_7_with_cache.json` now opens with `"_meta_note": "SYNTHETIC FIXTURE — replace via scripts/capture_response_fixture.py …"`. `Message.model_validate` accepts the extra (anthropic's base model allows extras), so the tripwire test still passes unchanged.
- **Cache-hit assertion in capture script**: `scripts/capture_response_fixture.py` now raises if `cache_read_input_tokens <= 0` on the second call. Prevents a cache-miss from silently producing a misleading fixture; you retry instead.
- **Task 021 Notes — LLM budget health panel**: read-only panel under sources health, aggregates `llm_runs` for the 50-rows-per-task and 10-`synthesize`-calls triggers. Real-time 90%-of-budget warnings continue to fire in logs; the panel surfaces the slower triggers. When any trigger fires → open an orchestrator request, don't tune silently.

**What I would do next without intervention**: Task 005 — Reddit ingester. `apfun/sourcing/reddit.py` with a sync `httpx.Client`, public JSON endpoints, content-hash dedup, polite rate limiting. First exercise of the verify-constants convention on a non-LLM surface — Reddit's documented rate limits, UA-string requirements, JSON endpoint pagination shape all get inline annotations or `# TODO verify` markers.

**Specific questions or risks**:

- **Task-number redirect**. Feedback 006 referenced "task 022" for the budget health panel, but the operational sources-health UI lives in task 021 (`021-admin-ui-sources-projects.md`); task 022 is the weekly digest email. I placed the note in 021 with a one-line flag explaining the redirect. Confirm 021 is the right home, or should the panel get a sibling task file of its own?
- **Synthetic-fixture detection in CI**. Right now the synthetic fixture parses cleanly through the tripwire test. Worst case: I forget to run `capture_response_fixture.py`, the synthetic file ships for weeks, and any SDK change that breaks the *real* shape but happens to match the synthetic shape silently passes. Worth a tiny test that asserts `_meta_note` is absent from the fixture (i.e., the fixture is real, not synthetic), or trust the convention + the banner?
- **Reddit's "verify against current docs"** call-out in feedback 006. Reddit changed their rate-limit policy in 2023 — 100 QPM for OAuth, 10 QPM for unauthenticated, and they've tightened periodically since. Task 005 starts with public-JSON-no-auth (per task spec), so the unauthenticated quota applies. I'll fetch `support.reddit.com` and Reddit's API docs to verify the current numbers before annotating. Flag if you'd prefer I aim for OAuth from the start (a few hours of extra setup but a much higher ceiling).
- **`_meta_note` survival across SDK versions**. Anthropic's `Message.model_validate` accepts `_meta_note` today because of `extra="allow"` (or similar). A future SDK release could switch to `extra="forbid"` and break the test. The "real capture" replacement closes that risk for good. Until then — accept the small fragility, or guard against it (e.g., strip `_meta_note` in the test before validating)?

**Relevant files/diffs**:

- commit `7e90c20` (the feedback-006 application)
- `apfun/llm/client.py` — TODO in `_maybe_warn_budget`, no-preemptive comment on `JUDGMENT_TASKS`
- `CLAUDE.md` — convention text tightened
- `tests/fixtures/opus_4_7_with_cache.json` — `_meta_note` banner
- `scripts/capture_response_fixture.py` — cache-hit assertion
- `docs/tasks/021-admin-ui-sources-projects.md` — budget health panel note
- `docs/orchestrator/INDEX.md` — 006 → answered
