# 005 — Reddit ingester

**Goal:** Pull recent posts from a configurable set of subreddits into `raw_signals`, deduped, rate-limited, with three-strikes auto-disable for dead/banned subs. First non-LLM exercise of the verify-constants and contract-test conventions.

**Complexity:** M

Depends on: 002.

## Deliverables

### Rate limiter (source-agnostic)

- `apfun/sourcing/_rate_limit.py`: `TokenBucket(rate_per_sec: float, burst: int)` with blocking `acquire()`. Source-agnostic; each ingester instantiates its own bucket with source-specific params. Per orchestrator feedback 008.

### Reddit ingester

- `apfun/sourcing/reddit.py`: sync function `ingest(session, source)` reading `source.config_json` for `{"subreddits": [...], "since_hours": 6, "fetch_kind": "new"|"top"}`.
- Fetches `https://www.reddit.com/r/<sub>/new.json?limit=100` (public JSON, no OAuth — feedback 007 Q3 confirmed for v1 volume) via `httpx.Client`.
- Module-level `_BUCKET = TokenBucket(rate_per_sec=3.5, burst=5)`; every HTTP call `_BUCKET.acquire()` first.
- Module-level `REDDIT_USERNAME = settings.reddit_username` (from `APFUN_REDDIT_USERNAME` env). If empty, **raise `RuntimeError` at module import / settings construction** — Reddit silently blocks non-conformant UAs and a missing username produces phantom-empty results, not errors. Don't warn; fail. Per feedback 008 Q2.
- `USER_AGENT = f"apfun-funnel:v0.1 (by /u/{REDDIT_USERNAME})"`.

### Constants — annotation pass

Apply the verify-constants convention from CLAUDE.md (both `# verified` and `# heuristic` forms):

- Reddit unauth QPM ceiling → `# verified YYYY-MM-DD <reddit-api-docs-url>`.
- UA-string format requirement → `# verified YYYY-MM-DD <reddit-api-rules-url>`.
- Listing endpoint pagination (25-item default, `after` cursor format) → `# verified YYYY-MM-DD <reddit-listings-docs>`.
- Polite rate target 3.5 req/s sustained, burst 5 → `# heuristic YYYY-MM-DD — community consensus: aim well under the 10 QPM ceiling, headroom for spikes`.
- Three-strikes auto-disable threshold → `# heuristic YYYY-MM-DD — balances responsiveness against transient single-day failures`.

### Dedup

- `content_hash = sha256(subreddit + external_id + title + body[:500])` per orchestrator feedback 007. Body slicing protects against minor edits triggering spurious new rows; the external_id keeps cross-subreddit reposts as distinct signals.
- Skip insert if `content_hash` already exists in `raw_signals` (the column is UNIQUE).

### Payload

- `payload_json` stores the **full** Reddit response object (per feedback 007: storage is cheap, schema flexibility is valuable).
- Capture-but-tag deletions (feedback 009 Q2): when `body == "[deleted]"` or `body == "[removed]"`, set:
  ```python
  payload_json["is_deleted"] = True
  payload_json["deletion_marker"] = "[deleted]" | "[removed]" | "<other>"
  ```
  Don't filter at ingest; Stage 1 clustering (task 010) decides weighting.

### Source health: three-strikes auto-disable

- New Alembic migration adds `consecutive_failures INT NOT NULL DEFAULT 0` to `sources`.
- `TERMINAL_STATUSES = frozenset({403, 404, 410})` — 410 Gone is the rarest, most explicit "permanently gone" signal (per feedback 010 Q2). Everything else (5xx, 429, timeouts, connection errors) logs but doesn't increment — those are about us or Reddit, not about the sub being dead.
- On any successful fetch for a source: reset `consecutive_failures = 0`.
- On a `status in TERMINAL_STATUSES` failure: increment.
- When `consecutive_failures >= 3 AND status in TERMINAL_STATUSES`: set `is_active = False` and log a WARNING.
- **UA-block guard** (feedback 010 Q2): if >50% of sources in a single batch return 403, treat as a global UA-block (our UA was malformed or blocked across Reddit) — log at ERROR, surface in `scheduler_runs`, and **don't increment per-source counters for that batch**. Avoids auto-disabling 10 subreddits because of an hour-long UA issue. Annotate the 50% threshold:
  ```python
  # heuristic 2026-05-18 — UA-block detection: if >50% of sources in one
  # batch return 403, treat as global block. Don't increment per-source
  # counters or auto-disable; the issue is our UA, not the subs.
  _UA_BLOCK_BATCH_FRACTION = 0.5
  ```

### Logging

- Structured INFO log per listing call: `{subreddit, status_code, items_returned, latency_ms, error_class?}`. The sources-health UI (task 021) will read this; even before that, the logs are what we'll grep when something stops working.

### Retries

- Three retries with exponential backoff for transient HTTP errors (5xx, 429, timeouts). Implemented inline in the ingester — accept code duplication with `apfun/llm/client.py` until task 007 (ProductHunt) is the third call site and the right abstraction shape is clear. Per orchestrator feedback 008.

### Tests

- **Unit tests** mock `httpx.Client` against `tests/fixtures/reddit/listing_*.json` captures. Verify dedup, deletion-tagging, three-strikes counter logic, UA format, rate-limit acquire calls.
- **Schema contract test** (per the new convention from feedback 009): `tests/unit/test_reddit_schema_contract.py` asserts the fields the ingester depends on (`kind`, `data.children[].data.{id, subreddit, title, selftext, score, num_comments, created_utc, permalink, url}`). Fails loudly if a fixture refresh shows Reddit changed the response shape.
- **Integration test** (`@pytest.mark.integration`, gated on internet access) hits one real subreddit, captures the response into `tests/fixtures/reddit/` for use by unit tests.
- **Fixture meta header** (per feedback 010 Q1): every captured fixture starts with a `_fixture_meta` field:
  ```json
  {
    "_fixture_meta": {
      "captured": "2026-05-18",
      "refreshed": "2026-08-12 — Reddit added media_metadata field; updated contract assertions to match",
      "source": "GET https://www.reddit.com/r/programming/.json"
    },
    ...
  }
  ```
  Strip `_fixture_meta` in tests before parsing (same pattern as `_meta_note` on the Anthropic fixture). The three-line audit trail makes "why did this fixture change?" answerable without git archaeology.

### Bootstrap

- `scripts/seed_sources.py` registers the brief's core subs (`SaaS`, `Entrepreneur`, `SmallBusiness`) + 10-20 vertical placeholders, marked `is_active=True`.

## Acceptance

- Fixture-backed unit test: N rows inserted on first run; 0 rows inserted on a second run of the same fixture (dedup works).
- Integration test (opt-in) hits one real subreddit and inserts ≥1 row, AND captures the response JSON for the contract test.
- Schema contract test green against the captured fixture.
- Three consecutive 404s on a source disable it (`is_active=False`); next successful fetch on a re-enabled source resets the counter.
- Module import without `APFUN_REDDIT_USERNAME` raises `RuntimeError`. Module import with the env var sets up the wrapper cleanly.

## Notes

- Reddit's public-JSON-no-auth endpoint is rate-limited (~10 QPM per IP). When we observe 429s with any frequency, that's the trigger to open an orchestrator request for OAuth migration — not a pre-emptive build (feedback 007 Q3).
- Captured fixtures may include user identifiers that look like PII (usernames, comment text). Treat the fixtures as you would the live data — they're committed to the repo but represent public content. If we ever need to scrub before commit, that's a separate concern.
