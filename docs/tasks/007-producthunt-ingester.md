# 007 — ProductHunt ingester

**Goal:** Capture recent launches across two surfaces (topic + leaderboard) into `raw_signals`. Third call site for the per-source ingester pattern — see feedback 013 Q1 for why this lands against the duplicated shape (unification follows in a separate refactor PR).

**Complexity:** S

Depends on: 002.

## Deliverables

### Ingester (`apfun/sourcing/producthunt.py`)

- GraphQL endpoint: `https://api.producthunt.com/v2/api/graphql` (per feedback 013 Q2 — predictable schema beats HTML scraping).
- Per-source `ingest(session, source) -> IngestResult`; batch-aware `ingest_batch(session, sources)`. Same shape as `apfun/sourcing/reddit.py` and `apfun/sourcing/hn.py` — accept duplication. The unification PR comes after this task lands (per feedback 013).
- Module-level `_BUCKET = TokenBucket(rate_per_sec=1.0, burst=2)` initially (`# heuristic` — ProductHunt doesn't publish unauth limits cleanly; conservative start, tune after observing 429s).
- Cursor-based GraphQL paging — `posts(first: N, after: <cursor>)`.

### Auth

- Env var `APFUN_PRODUCTHUNT_TOKEN`, **Client-only token** (read-only; long-lived; less sensitive if leaked). Per feedback 013 Q2.
- Empty default in `config.py`. Fail-loud at the **call site**, not at `Settings()` construction — ProductHunt returns 401 with a meaningful body on missing/invalid tokens (loud-failure category in the new "Auth secret discipline" convention).
- Missing-token path (per task 007 acceptance + feedback 013 spec note): the ingester does NOT crash. It logs at WARNING, returns an `IngestResult` with `items_captured=0` and `error_class="missing_token"`, and the batch wrapper writes a `scheduler_runs` row with `ok=True, items_processed=0, error=None, notes` reflecting the no-op. Rationale: missing token is an operator/config issue, not a runtime fault — the scheduler should keep running, and the operator notices via the warning + zero-row run.

### Source config

```jsonc
{
  "surface": "topic" | "leaderboard",
  "topics": ["productivity", "developer-tools"],          // when surface=topic
  "leaderboard": "daily" | "weekly" | "monthly",           // when surface=leaderboard
  "n_days": 1,                                             // how far back to pull
  "min_votes_count": 10                                    // configurable filter; see below
}
```

Two-surface design (per feedback 013 heads-up): topic surface catches newer launches under specific verticals; leaderboard surface catches the high-attention curated set. Different filtering needs across the two. Seed sources will include at least one of each.

### Constants — annotation pass

- ProductHunt GraphQL endpoint URL → `# verified YYYY-MM-DD https://api.producthunt.com/v2/docs` (or fallback citation if the docs page has moved).
- Rate limit `1.0/s, burst 2` → `# heuristic YYYY-MM-DD — ProductHunt doesn't publish unauth limits; conservative start, retune after 429 observations.`
- TERMINAL_STATUSES for GraphQL: `{401, 403, 404}` — 401 from token revocation, 403 from quota exhaustion, 404 from invalid query. `# verified YYYY-MM-DD — GraphQL APIs follow HTTP status conventions for top-level auth/permission errors.`
- Vote-count default threshold (TBD; lean 10 for `topic` surface, lower for `leaderboard` since curation already filters) → `# heuristic YYYY-MM-DD — per feedback 013 heads-up; tune later.`

### Dedup

- `content_hash = sha256(slug)`. The slug is ProductHunt's stable canonical identifier for a launch.

### Payload

- `payload_json` stores the full GraphQL `Post` object including: `name`, `tagline`, `description`, `slug`, `url`, `votesCount`, `commentsCount`, `featuredAt`, `topics.edges[].node.{name,slug}`, `makers.edges[].node.username`.
- Tag the surface that surfaced the post: `payload_json["_apfun_surface"] = "topic" | "leaderboard"` (mirrors HN's `_apfun_query` pattern).

### Retries

- Three retries with exponential backoff for 5xx, 429, timeouts. Inline (per the established duplication discipline — unification PR follows this task).

### Vote-count filter

- Configurable per source. Default 10 for `topic` surface, 5 for `leaderboard`. Filters out the long tail of low-attention launches before ingest. Per feedback 013 heads-up — ProductHunt has many low-signal launches.

### Tests

- **Unit tests** mock `httpx.Client` against `tests/fixtures/producthunt/posts_*.json` captures. Verify dedup, vote-count filter, surface tagging, retry logic, missing-token no-op path.
- **Schema contract test** (`tests/unit/test_producthunt_schema_contract.py`) asserts the GraphQL response shape — `data.posts.edges[].node.{id, slug, name, tagline, description, votesCount, commentsCount, featuredAt, url, topics, makers}`.
- **Batch-wrapper test** (`tests/unit/test_producthunt_ingest_batch.py`) — counter increments, three-strikes auto-disable, scheduler_runs row, missing-token batch behavior.
- **Integration test** (`tests/integration/test_producthunt_live.py`, `@pytest.mark.integration`, gated on `APFUN_PRODUCTHUNT_TOKEN` being set and internet available).
- **Capture script** (`scripts/capture_producthunt_fixture.py`) — separate from the integration test (capture is intentional, not a side effect).

### Bootstrap

- `scripts/seed_sources.py` extended with two ProductHunt sources: one topic-surface (productivity + developer-tools), one leaderboard-surface (daily).

## Acceptance

- Fixture-backed unit test: rows inserted on first run; 0 rows inserted on the second run of the same fixture (dedup works).
- Vote-count filter: setting `min_votes_count=1000` to a fixture with all-below-1000 posts inserts zero rows.
- Missing-token path: with `APFUN_PRODUCTHUNT_TOKEN=""`, `ingest()` returns `IngestResult(items_captured=0, error_class="missing_token")`, logs a WARNING, and the batch wrapper writes a `scheduler_runs` row with `ok=True, items_processed=0`.
- Schema contract test green against the captured fixture.
- Integration test (opt-in) hits real ProductHunt and inserts ≥1 row.
- `grep -r '# TODO verify' apfun/ tests/ scripts/` returns zero at task end.

## Notes

- "Negative space" (what's *missing*) is harder than what's launched — capture launches only in this task. Stage 1 clustering (task 010) is responsible for inverting them into gaps.
- Token-scope choice (Client-only over User-context) is per feedback 013 Q2. If we ever need user-context queries (e.g., user-private collections), that's a separate token + separate task.
- After this task merges: open `feature/refactor-sourcing-base` (no task number; behavior-preserving) per feedback 013 action item #4. The three implementations should be similar enough that the refactor is a small, mechanical PR.
