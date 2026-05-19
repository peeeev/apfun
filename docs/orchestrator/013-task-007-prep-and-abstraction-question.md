# Request 013: task 007 prep — abstraction question + ProductHunt API choice

**Date:** 2026-05-19

**Context**: Task 005 (Reddit) merged into `main` via PR #1; task 006 (HN) is up as PR #2 awaiting merge. TokenBucket held against the second consumer — no escalation needed there. With two ingesters now in hand and task 007 (ProductHunt) being the explicit "third call site triangulation" gate from feedback 008, this is the moment to ask the abstraction question deliberately before any third implementation entrenches the duplication further.

## What landed in tasks 005 and 006

(The orchestrator can't see the PRs directly; summarizing here.)

### Task 005 — Reddit ingester (merged at commit `2d964d9`)

- **`apfun/sourcing/_rate_limit.py`** — source-agnostic `TokenBucket(rate_per_sec, burst)` with thread-safe blocking `acquire()`. Each ingester instantiates its own bucket.
- **`apfun/sourcing/reddit.py`** — per-source `ingest()` returns `IngestResult(source_id, items_captured, status_codes: list[int], error_class, latency_ms)`; batch-aware `ingest_batch()` runs `ingest()` across sources, applies the **>50%-batch UA-block guard** (`_UA_BLOCK_BATCH_FRACTION = 0.5`), runs three-strikes auto-disable on `TERMINAL_STATUSES = {403, 404, 410}`, writes a `scheduler_runs` row. Module-level `_BUCKET = TokenBucket(rate_per_sec=3.5, burst=5)`, `USER_AGENT = f"apfun-funnel:v0.1 (by /u/{settings.reddit_username})"`. Content-hash on `(subreddit, external_id, title, body[:500])`. Capture-but-tag `[deleted]`/`[removed]` rows.
- **`apfun/config.py`** — `reddit_username` field with custom `field_validator` that fails loud at `Settings()` construction (Reddit silently degrades on missing/empty UA — phantom-empty results are worse than crashes). The validator's error message cites CLAUDE.md → Networking.
- **`apfun/models/source.py` + Alembic migration `cf695f497312`** — `consecutive_failures INT NOT NULL DEFAULT 0` on `sources`.
- **`scripts/capture_reddit_fixture.py`** — capture-only, separate from the integration test (per feedback 011 Q3). Writes `tests/fixtures/reddit/listing_*.json` with `_fixture_meta` triplet (`captured` / `refreshed` / `source`); on refresh, forwards the prior `captured` date into the new `refreshed` slot.
- **`scripts/seed_sources.py`** — idempotent seeder for `r/SaaS`, `r/Entrepreneur`, `r/smallbusiness` + ~15 vertical placeholders.
- **`tests/conftest.py`** — sets `APFUN_REDDIT_USERNAME=apfun_test_runner` before any apfun import so the fail-loud validator is exercised without breaking test collection.
- **Two `# heuristic` constants with no authoritative URL** (per feedback 012): `_REDDIT_UNAUTH_QPM_CEILING = 10` (community-reported via r/redditdev; sanity-check only) and `_USER_AGENT` format (PRAW/snoowrap community convention). `grep -r '# TODO verify'` returns zero across the repo.
- **Tests**: 5 TokenBucket + 8 ingester unit + 7 batch-wrapper unit + 3 schema contract + 1 integration. All green.

**Decisions traced through feedback 007 → 012.** Spec finalized in 5 orchestrator turns before implementation; implementation itself produced no design surprises.

### Task 006 — HN ingester (PR #2 at `9be120d`, branch `feature/task-006-hn-ingester`)

- **`apfun/sourcing/hn.py`** — Algolia HN search ingester. Same module shape as Reddit: `IngestResult` + `ingest` + `ingest_batch`. Module-level `_BUCKET = TokenBucket(rate_per_sec=1.5, burst=3)`, static `USER_AGENT = "apfun-funnel/0.1 (https://apfun.online)"` (no fail-loud needed — HN doesn't UA-block). `TERMINAL_STATUSES = {400, 401, 403, 404}` — Algolia's 4xx generally means malformed query (our config bug) so fail the source to surface it. **No UA-block batch guard** — `_apply_health_update` is the Reddit version minus the UA-block branch. `content_hash = sha256(objectID)`. Payload preserves the full Algolia hit and adds `_apfun_query` recording which configured query surfaced it. **Points-threshold filtering** (default story≥3, comment≥1, both config-overridable via `min_story_points`/`min_comment_points`).
- **`scripts/capture_hn_fixture.py`** — capture-only, mirrors the Reddit script. Same `_fixture_meta` triplet with prior-date forwarding.
- **`scripts/seed_sources.py`** — extended (not replaced) with three HN query bundles using the opportunity-revealing phrasings from the task spec: `hn:wishes` (`tool you wish existed`, `I wish there were`, `what software is missing`), `hn:ask-hn` (`Ask HN: what tool/SaaS/software`), `hn:alternatives` (`alternatives to`, `self-hosted alternative to`, `open source alternative to`). Each bundle is one Source row; the source's queries run sequentially in a single `ingest()` call.
- **Tests**: 5 schema contract + 10 ingester unit + 5 batch-wrapper unit + 1 integration. Plus the existing 52 from prior tasks → **73 unit tests pass total**, 0 pyright errors, ruff clean, zero `# TODO verify`.

**TokenBucket finding**: held cleanly. No new burst semantics, blocking modes, or async needs surfaced. Feedback 012's "escalate before generalizing" gate didn't trigger.

**No spec/feedback surprises during 006.** Task 006 was sized "S" and went start-to-PR in one feature branch without any orchestrator escalation between feedback 012 and now.

## Duplication snapshot — what's the same across `reddit.py` and `hn.py`

After two implementations, the shape is:

**Identical structure (template-shaped):**
- `@dataclass class IngestResult` — exact same fields
- Module-level constants: `_BUCKET = TokenBucket(...)`, `_MAX_RETRIES`, `_RETRY_BASE_DELAY_S`, `_AUTO_DISABLE_THRESHOLD`, `TERMINAL_STATUSES: frozenset[int]`
- `_fetch_X(client, ...)` retry loop — only the URL/params construction and "what counts as terminal" differ
- `ingest(session, source, client=None) -> IngestResult` — same skeleton: parse config, iterate sub-units (subreddits/queries), per-unit fetch + parse + insert, build result
- `_insert_signal(session, source, ...)` — same SQLAlchemy pattern + `IntegrityError` → dedup
- `ingest_batch(session, sources, job_id=, client=None)` — same outer shape: per-source try/except, results list, `_apply_health_update` per source, `scheduler_runs` row at the end
- `_apply_health_update(source, result)` — *identical except Reddit has a UA-block-skip branch*

**Per-source (genuinely different):**
- URL template, query params, headers
- `_content_hash` input shape (Reddit: `(subreddit, external_id, title, body[:500])`; HN: `(objectID,)`)
- Payload mutations: Reddit tags `is_deleted`/`deletion_marker`; HN tags `_apfun_query`
- Filtering: HN has points threshold; Reddit has nothing equivalent
- Status-code → terminal mapping (different sets)
- Rate-limit parameters

**Already shared:** `TokenBucket` itself, `RawSignal` / `Source` / `SchedulerRun` models, the verify-constants convention. Those are working.

Rough numbers: `reddit.py` is 372 lines, `hn.py` is 304 lines. If I trace the structural-identical parts: probably 120-150 lines of skeleton that would be one module if unified, plus per-source hook implementations.

## Specific questions or risks

### Q1 — Unify-now vs unify-after-007 (the main question)

My lean: **write ProductHunt against the current duplication, then unify in a dedicated task 007.5 (or fold into 007's PR if the unification is small).**

Reasons for that lean:

- **Feedback 008 said "third call site is when the right abstraction shape is clear."** Taking that literally means *after we've seen ProductHunt land concretely*, not "we have two and can guess the third." The whole point of waiting for three was to avoid premature abstraction shaped by the first two.
- **The current duplication isn't entangled.** Each module is self-contained and readable. The cost is repetition (~150 lines), not coupling. That's a low pain bar.
- **ProductHunt's shape might genuinely diverge.** GraphQL paging, different rate-limit model, OAuth-token rotation — any of these could change what "fetch with retry" wants to look like. If I unify on what Reddit + HN agree on, I might bake in assumptions that ProductHunt has to fight.
- **Refactoring 3 → 1 is one PR.** Splitting it into "unify Reddit+HN first, then add ProductHunt" is two PRs and forces the third implementation to navigate around the shape I just chose.

Reasons that lean might be wrong:

- **ProductHunt with no unification means three messy files.** If the third one looks 90% like the second, I will have wasted effort.
- **Test-side duplication scales linearly too** — each new source adds `test_X_schema_contract.py`, `test_X_ingester.py`, `test_X_ingest_batch.py`. We're not unifying tests at three; do we *want* the option to share test helpers?

**Confirm/redirect.** If you want unification before 007, the natural shape is `apfun/sourcing/_base.py` with `BaseIngester` (abstract `fetch_unit`, `parse_hit`, `should_keep`, etc.) + `ingest_batch_template`. I'd cut that as a prep commit before starting 007.

### Q2 — ProductHunt API choice: GraphQL (token-auth) vs scraping public discover pages

Spec for task 007 is sparse. The two real options:

- **(a) GraphQL API** at `https://api.producthunt.com/v2/api/graphql`. Requires a developer token (free, application via the PH developer portal). Cleaner — schema-typed responses, no HTML parsing.
- **(b) Public discover pages** — `https://www.producthunt.com/leaderboard/daily/...` and similar. No auth, fragile to HTML changes, heavier on us.

Lean **(a) GraphQL with developer token.** Justifications:
- Predictable response shape → easier to write a contract test against
- Lower rate-limit pressure (GraphQL is intended for programmatic use; HTML scraping isn't)
- No HTML parsing fragility
- Schema-versioned, so contract test catches breakage cleanly

Tradeoff: introduces a new secret to manage (`APFUN_PRODUCTHUNT_TOKEN`). Mirroring the Anthropic pattern (empty default, fail-loud at first use, not at `Settings()` construction) seems right — not every dev environment needs PH access for local work, same as not every dev environment has an Anthropic key.

Confirm or push back.

### Q3 — Auth-secret-handling pattern across the project

We now have three auth modes:

- **`APFUN_REDDIT_USERNAME`** — fail-loud at `Settings()` construction (Reddit silently degrades on missing UA → phantom-empty results are worse than crashes).
- **`APFUN_ANTHROPIC_API_KEY`** — empty default, fail-loud at first API call (LLM client can be imported without it; tests construct `LLMClient` with a mock).
- **`APFUN_PRODUCTHUNT_TOKEN`** (proposed) — same shape as Anthropic.

Worth documenting this as a CLAUDE.md convention? Something like:

> **Auth secret discipline.** External-service secrets are env vars under the `APFUN_` prefix. The fail-loud point depends on the failure mode of "secret missing at runtime":
> - **Silent degradation** (third-party returns nonsense / wrong-account results / etc.) → fail at `Settings()` construction with a CLAUDE.md-pointing message.
> - **Loud failure** (third-party rejects the request with a clear error) → empty default; fail at first call site with a clear message.
>
> Reddit username is silent-degradation; Anthropic key and ProductHunt token are loud-failure.

If you'd like that as a convention, I'd add it to CLAUDE.md as part of task 007's prep commit. Or skip — the pattern is already visible in `config.py` and the call sites.

### Q4 — Test-side duplication

Already mentioned in Q1, but worth surfacing separately: `test_reddit_ingester.py` and `test_hn_ingester.py` share patterns (mock `httpx.Client`, fixture load, dedup test shape, retry-status tests). Test helpers like `_make_mock_client` are duplicated.

Lean: **leave test-side duplication alone for now.** Test code is the part I most want to be obvious-and-self-contained when debugging a flake. Sharing helpers across test files is a regret pattern. Confirm or push back.

**What I would do next without intervention**:

1. If you confirm "write ProductHunt first, unify after": cut `feature/task-007-producthunt-ingester` and start the ingester following the established (duplicated) shape. Use GraphQL + token. Add the secret to `config.py` following the Anthropic pattern.
2. If you say "unify first": cut `feature/sourcing-base-ingester` (no task number — it's a refactor) and pull the template shape into `apfun/sourcing/_base.py` with a clean diff that keeps `reddit.py` and `hn.py` passing their existing tests. Then start task 007 on the unified base.

**Relevant files/diffs**:

- branch `feature/task-006-hn-ingester` at `9be120d` — PR #2
- `apfun/sourcing/reddit.py` (372 lines) vs `apfun/sourcing/hn.py` (304 lines) — diff `git diff main:apfun/sourcing/reddit.py feature/task-006-hn-ingester:apfun/sourcing/hn.py` for the side-by-side structural comparison
- `docs/tasks/007-producthunt-ingester.md` — current spec (light)
- `docs/orchestrator/INDEX.md` — row 013 → open after this commit
