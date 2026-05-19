# Request 011: feedback-010 applied — beginning task 005 implementation

**Date:** 2026-05-19

**Context**: Feedback 010 landed at commit `ef7cfb1` (local; push to `origin/main` pending — see note at end). Four small folds, no separate prep commit per the feedback's explicit guidance. All design questions for task 005 are now resolved; spec is fully derived from feedback 007/008/009/010. Starting implementation next.

**What I just did**:

- **CLAUDE.md → Conventions → Verify external constants inline**: appended "TODO verify resolution" postscript. When a `# verified` URL can't be sourced in-PR, use `# TODO verify by end of task <NNN>: <reason>` instead. Grep must be zero at task end or items get escalated.
- **`docs/tasks/005-reddit-ingester.md` → Source health**: replaced ad-hoc 403/404 increment with `TERMINAL_STATUSES = frozenset({403, 404, 410})`. Added the >50%-batch UA-block guard with `_UA_BLOCK_BATCH_FRACTION = 0.5` carrying its `# heuristic 2026-05-18 — ...` annotation inline.
- **`docs/tasks/005-reddit-ingester.md` → Tests**: added the `_fixture_meta` header pattern (`captured` / `refreshed` / `source` triplet, stripped in tests like `_meta_note` is for the Anthropic fixture).
- **`docs/tasks/023-github-actions-ci.md` → Notes**: spelled out *why* `contents: read` is the right choice — auto-committing captured fixtures would silently launder API flakiness into the test contract; the fixture-as-contract pattern requires a human-review moment.
- **`docs/orchestrator/INDEX.md`**: row 010 → `answered`.

**What I would do next without intervention**:

Begin task 005 implementation per the two-commit plan from feedback 010:

1. **`005a: rate limiter + sources.consecutive_failures column`**
   - `apfun/sourcing/__init__.py` (empty package marker if not already present).
   - `apfun/sourcing/_rate_limit.py`: source-agnostic `TokenBucket(rate_per_sec: float, burst: int)` with blocking `acquire()`. No source-specific knowledge inside.
   - Alembic migration: `consecutive_failures INT NOT NULL DEFAULT 0` on `sources`. Reuse the existing batch-mode pattern.
   - Update `apfun/models/source.py` to mirror the new column.
   - `tests/unit/test_rate_limit.py`: token replenishment math, burst semantics, blocking on empty bucket (use `monkeypatch.setattr` against `time.monotonic` + `time.sleep` to keep the test fast and deterministic).

2. **`005b: Reddit ingester`**
   - `apfun/config.py`: add `reddit_username` (env: `APFUN_REDDIT_USERNAME`). Validator: empty/missing must raise — Pydantic `Field(...)` (no default) handles it; the import-time crash is exactly the fail-loud behavior we want.
   - `apfun/sourcing/reddit.py`: sync `ingest(session, source)`, module-level `_BUCKET = TokenBucket(rate_per_sec=3.5, burst=5)`, module-level `USER_AGENT` composed from settings. Content-hash on `(subreddit, external_id, title, body[:500])`. Capture-but-tag `[deleted]`/`[removed]` via `is_deleted`/`deletion_marker` keys. Three retries with exponential backoff inline (accept duplication with `apfun/llm/client.py` until task 007 triangulates the abstraction shape).
   - `tests/fixtures/reddit/listing_*.json`: small synthetic fixtures with `_fixture_meta` headers (real one captured by integration test on first dispatch).
   - `tests/unit/test_reddit_ingester.py`: dedup, deletion tagging, three-strikes counter behavior, UA format, rate-limit `acquire()` calls, UA-block batch guard (mock >50% 403s, assert no per-source counter increments).
   - `tests/unit/test_reddit_schema_contract.py`: per the new convention, assert the fields the parser depends on against the captured fixture.
   - `tests/integration/test_reddit_live.py` (`@pytest.mark.integration`): hit one real subreddit, capture the response with proper `_fixture_meta`.
   - `scripts/seed_sources.py`: registers `SaaS`, `Entrepreneur`, `SmallBusiness` + ~12 vertical placeholders, all `is_active=True`.

**Specific questions or risks**:

1. **Pydantic v2 `Field(...)` vs custom validator for `reddit_username`**. The fail-loud rule says missing/empty raises at import-time. Pydantic v2's `Field(...)` raises a `ValidationError` on missing required field at `Settings()` construction; that *is* import-time since `settings = Settings()` is module-scoped in `apfun/config.py`. Good enough, or do you want a more explicit raise with a custom message pointing back to CLAUDE.md (mirroring how the host validator rejects `127.0.0.1`)? Leaning explicit validator — the custom message is what makes the error self-documenting on a tired Monday morning.

2. **Where the UA-block batch-aware logic lives**. The natural place is *not* inside `ingest()` (which sees one source) but in a small batch wrapper — call it `ingest_batch(session, sources)` — that runs `ingest()` per source, tallies the 403s, applies the guard, and writes the `scheduler_runs` row. The per-source `ingest()` returns a result struct rather than mutating `consecutive_failures` directly. The batch wrapper decides whether to increment. Confirms the "ingester is dumb, scheduler is smart" separation. Sound? Or do you want a single-source `ingest()` to also be batch-aware via context (e.g., a `_batch_state` arg)?

3. **Integration test that writes a fixture**. Convention is: the integration test *captures* a fixture in the same run that exercises it. But pytest integration tests run with `make test-all` (paid LLM tier), not with the Reddit-only path. Two ways to read this:
   - **(a)** A separate `scripts/capture_reddit_fixture.py` (mirrors `scripts/capture_response_fixture.py`) — explicit one-off, run when needed.
   - **(b)** The integration test itself writes the fixture as a side effect — gated on `@pytest.mark.integration`, so opt-in.
   Lean **(a)** for symmetry with the Anthropic capture flow and to avoid integration-test-as-fixture-generator confusion. The integration test then just asserts ≥1 row inserted against a real subreddit, no fixture-write responsibility. Confirm?

4. **`# verified` URLs for Reddit constants**. The convention says reach for the authoritative source first; fall back to `# TODO verify by end of task 005: <reason>` when no current URL resolves. Reddit's docs have shifted enough that I expect at least one constant to land as TODO-verify on first pass — most likely the unauth QPM ceiling, since Reddit doesn't publish that number cleanly. If the TODO grep is non-zero at end of task, the new convention says escalate to orchestrator. Setting expectations: I'll likely come back with 1-2 unresolved-URL items at end of 005b.

**Relevant files/diffs**:

- commit `ef7cfb1` (feedback-010 application, local — push pending per note below)
- `CLAUDE.md` — TODO verify resolution postscript
- `docs/tasks/005-reddit-ingester.md` — TERMINAL_STATUSES, UA-block guard, `_fixture_meta` header
- `docs/tasks/023-github-actions-ci.md` — Notes addition for `contents: read` rationale
- `docs/orchestrator/INDEX.md` — row 010 → answered

**Operational note**:

The classifier blocked the direct push of `ef7cfb1` to `origin/main`; commit exists locally only. All prior feedback-application commits (003, 004, 005, 006, 007, 008, 009 applications + corresponding orchestrator-request commits) landed via the same direct-to-main path without issue, so the policy change is new. If you'd like me to switch to a working-branch + PR cadence for this kind of docs commit going forward, that's straightforward — say the word and I'll cut `feature/feedback-010` and open the PR. Otherwise I'll wait for you to either authorize the push or run `git push origin main` yourself.
