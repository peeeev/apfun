# Request 009: feedback-008 applied — pre-task-005 ready

**Date:** 2026-05-18

**Context**: Feedback 008 received and applied at commit `cbf499b`. Two small CLAUDE.md edits landed; Q2/Q3 design choices and the aside items are baked into my task 005 plan. `make check` remains intentionally red on the synthetic-fixture forcing function (29 pass / 1 by-design fail); no other regressions. Ready to start task 005 implementation.

**What I just did**:

- **Convention extended** (feedback 008 Q4): the "Verify external constants inline" rule now recognizes two annotation forms:
  - `# verified YYYY-MM-DD <source-url>` — authoritative source exists.
  - `# heuristic YYYY-MM-DD — <rationale>` — judgment-derived from incomplete information.
  Distinct keywords so `grep -r '# heuristic'` can audit judgment values across the codebase. Lessons learned entry updated to reflect both forms.
- **Workflow note for the red gate** (feedback 008 Q1): CLAUDE.md → Tests now documents what the `test_fixture_is_real_capture` failure means and how to resolve it. Workflow: use subset gates (`pytest tests/unit/test_<area>*.py`) to verify task-specific work; the single intentional failure unblocks other work.
- **INDEX row 008** → answered with the four decisions summarized.

**What I would do next without intervention**: Task 005 — Reddit ingester. My plan after feedback 007/008 input:

- **`apfun/sourcing/_rate_limit.py`** — source-agnostic `TokenBucket(rate_per_sec, burst)` with `acquire()` (blocks if needed). Reddit ingester instantiates its own bucket; HN / ProductHunt will get their own when those tasks land. The third call site (task 007) will tell us if an abstraction layer above `TokenBucket` is warranted — premature abstraction skipped per the aside.
- **`apfun/sourcing/reddit.py`** — sync `httpx.Client`, function `ingest(session, source)`. Reads `source.config_json` for `{subreddits, since_hours, fetch_kind}`. Content-hash on `(subreddit, external_id, title, body[:500])` to catch dedup-after-edit. `payload_json` stores the full Reddit response. Structured per-call logging at INFO with `{subreddit, status_code, items_returned, latency_ms, error_class?}`.
- **Reddit UA, fail-loud**: `APFUN_REDDIT_USERNAME` env var required; missing value raises `RuntimeError` at module import / settings construction (not a runtime warning) — Reddit silently blocks non-conformant UAs and zero-results-as-success is worse than a crash. `.env.example` gets a placeholder line.
- **Three retries with exponential backoff** in the ingester, separate from the rate limiter. Code duplicated with `apfun/llm/client.py` for now; task 007 (ProductHunt) is the third call site that'll either prompt an abstraction or confirm the duplication is fine.
- **Constants annotation pass**, applying both `# verified` and `# heuristic` forms:
  - Reddit's unauth QPM ceiling → `# verified` against the current Reddit API docs / data-api-terms.
  - UA-string format `<platform>:<app-id>:<version> (by /u/<username>)` → `# verified` against Reddit's API rules doc.
  - Polite rate target ~3.5 req/sec sustained, burst 5 → `# heuristic` with community-consensus rationale and headroom argument.
  - JSON pagination shape (25-item default, `after` cursor) → `# verified` against Reddit's listing endpoint docs.
- **Tests**: real Reddit JSON captured into `tests/fixtures/reddit/` via an `@pytest.mark.integration` test; unit tests mock `httpx.Client` against those fixtures.

**Specific questions or risks**:

- **CI on GitHub** — currently no `.github/workflows/` exists. When we add CI (probably task 022 era), `make check` will stay red on the forcing function and block PR merges. Should I pre-flag in a task 022 (or sibling CI) note that GitHub Actions config should exclude `test_fixture_is_real_capture` (or skip when no API key is present), or trust we'll handle it when CI is wired up?
- **Reddit "deleted" / "removed" content**. When a post is deleted by the author or removed by mods, Reddit's JSON still returns the row with `body="[deleted]"` or similar markers. Treat as a regular signal (it tells us something about the topic), or skip (no real complaint content)? Lean *capture but tag* — set `payload_json.is_deleted = true`. Sound right?
- **Subreddit going private/banned mid-stream**. Status code 403 or 404 with a specific body. Plan: catch and log, set `source.last_error`, don't crash the whole ingest. Continue with other subreddits. Confirm.
- **Convention completeness check**. The convention now covers external numeric/string constants. What about external *schema shapes* (e.g., "Reddit listing JSON has `data.children[].data.id`")? Those are pinned by my code parsing them; if Reddit changes the shape, my code breaks. Worth a different form (e.g., a fixture-locked contract test), or leave to fixtures+real-API integration tests catching breakage?

**Relevant files/diffs**:

- commit `cbf499b` (feedback-008 application)
- `CLAUDE.md` — convention extended, red-state workflow note, Lessons entry updated
- `docs/orchestrator/INDEX.md` — row 008 → answered
