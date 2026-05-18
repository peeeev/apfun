# Request 010: feedback-009 applied — task 005 spec finalized

**Date:** 2026-05-18

**Context**: Feedback 009 received and applied at commit `f2c6a25`. Three action items landed: contract-test convention added as sibling to verify-constants, `docs/tasks/023-github-actions-ci.md` created and slotted into the overview, `docs/tasks/005-reddit-ingester.md` fully spec'd with every design choice from feedback 007/008/009 baked in. `make check` remains intentionally red on the single synthetic-fixture forcing function (29 pass / 1 by-design fail). Ready to start task 005 implementation.

**What I just did**:

- **CLAUDE.md → Conventions**: new bullet "Contract tests for external schemas." Sibling rule to verify-constants. Schema shapes (Reddit JSON paths, ProductHunt GraphQL types, G2/Capterra HTML structures) get `tests/unit/test_<source>_schema_contract.py` tests against captured fixtures. SDK-shipped models get tripwire tests via `model_validate`. The three-mechanism split (VALUES → annotations, SCHEMA SHAPES → contract tests, SDK-shipped MODELS → tripwire tests) is also captured as a Lessons-learned entry — "pick the mechanism by what changes."
- **`docs/tasks/023-github-actions-ci.md`**: new task spec, Complexity S. `.github/workflows/check.yml` (push/PR → `make check`), `.github/workflows/integration.yml` (`workflow_dispatch` → `make test-all`), and the CI-aware skip for `test_fixture_is_real_capture` so PRs aren't blocked while the synthetic fixture is being arranged out-of-band. Sequencing note in the file: numerically 023 but execution-order between Phase E (019) and Phase F (020).
- **`docs/tasks/000-overview.md`**: added "Cross-cutting" section with task 023 + the sequencing note.
- **`docs/tasks/005-reddit-ingester.md`**: comprehensive rewrite. Every design choice from feedback 007/008/009 is baked in — source-agnostic `TokenBucket` at `apfun/sourcing/_rate_limit.py`, fail-loud `APFUN_REDDIT_USERNAME` at import/settings time, UA format `apfun-funnel:v0.1 (by /u/<username>)`, content-hash on `(subreddit, external_id, title, body[:500])`, full `payload_json` with `is_deleted`/`deletion_marker` tagging on `[deleted]`/`[removed]` rows, structured INFO logging shape `{subreddit, status_code, items_returned, latency_ms, error_class?}`, three-strikes auto-disable on a new `sources.consecutive_failures` column (with migration), three retries with exponential backoff (separate from rate limiter, code duplication with LLM wrapper accepted until task 007 triangulates), explicit `# verified`/`# heuristic` annotations on every external constant, schema contract test against captured Reddit fixtures, integration test that captures the fixture in the same run.
- **INDEX.md** row 009 → `answered` with the four decisions summarized.

**What I would do next without intervention**: Implement task 005. The spec is now fully derived from orchestrator feedback — no design questions remaining. Expected commits:
1. `005a: rate limiter + sources.consecutive_failures column` — `apfun/sourcing/_rate_limit.py`, alembic migration, unit tests for `TokenBucket`.
2. `005b: Reddit ingester` — `apfun/sourcing/reddit.py`, config additions (`reddit_username`), bootstrap script, unit tests against fixtures, schema contract test, integration test that captures the live fixture.

**Specific questions or risks**:

- **Contract-test fixture refresh cadence**. The convention is silent on when to refresh captured fixtures. Options: (a) opportunistically — when something breaks or a developer touches that source's code; (b) periodically — at a calendar interval; (c) on every `make test-all` run that hits real API. (c) is automatic but couples fixture freshness to integration runs that already cost money; (a) is what's implicit today. Lean (a) for now, formalize if drift bites us. Confirm or push back.
- **`consecutive_failures` increment scope**. Plan increments on 403/404 specifically (terminal-looking). On 500-class or 429: log but don't increment (transient). On timeout: log but don't increment (transient). Is that the right split? E.g., a sustained 500 streak would suggest Reddit's own outage rather than a dead sub, so not-incrementing seems right. But a 429 streak suggests we're hammering — different concern, but again not a "dead sub" signal so still not-incrementing. Confirm.
- **Task 023 — `permissions: contents: read`** in the workflow. The skip-condition reads `os.getenv("CI")`. GitHub Actions sets `CI=true` automatically; no further setup. If we ever want the integration workflow to commit a fixture back to the repo (auto-capture from a CI dispatch), we'd need `contents: write` — but that's not in scope for task 023; flag for whenever.
- **First-implementation cost of `# verified` annotations**. Each Reddit external constant needs a real URL to cite. The feedback's links (`support.reddithelp.com`, `redditinc.com/policies/data-api-terms`, the API rules doc) — I'll hit each during implementation. If any current URL doesn't resolve (Reddit's docs structure shifts frequently), I'll `# TODO verify <best-known-source>` with a deadline in the same task — okay, or hard-fail-and-ask?

**Relevant files/diffs**:

- commit `f2c6a25` (feedback-009 application)
- `CLAUDE.md` — contract-test convention bullet, three-mechanism Lessons entry
- `docs/tasks/023-github-actions-ci.md` — new task file
- `docs/tasks/000-overview.md` — cross-cutting addition
- `docs/tasks/005-reddit-ingester.md` — full rewrite
- `docs/orchestrator/INDEX.md` — row 009 → answered
