# 005 — Reddit ingester

**Goal:** Pull recent posts from a configurable set of subreddits into `raw_signals`, deduped, rate-limited, with three-strikes auto-disable for dead/banned subs. First non-LLM exercise of the verify-constants and contract-test conventions.

**Complexity:** M

Depends on: 002.

## Deliverables

### Rate limiter (source-agnostic)

- `apfun/sourcing/_rate_limit.py`: `TokenBucket(rate_per_sec: float, burst: int)` with blocking `acquire()`. Source-agnostic; each ingester instantiates its own bucket with source-specific params. Per orchestrator feedback 008.

### Reddit ingester

- `apfun/sourcing/reddit.py`: sync `ingest(session, source) -> IngestResult` reading `source.config_json` for `{"subreddits": [...], "since_hours": 6, "fetch_kind": "new"|"top"}`. Returns a result struct (see "Ingester/scheduler split" below) — does NOT mutate `consecutive_failures` directly.
- Fetches `https://www.reddit.com/r/<sub>/new.json?limit=100` (public JSON, no OAuth — feedback 007 Q3 confirmed for v1 volume) via `httpx.Client`.
- Module-level `_BUCKET = TokenBucket(rate_per_sec=3.5, burst=5)`; every HTTP call `_BUCKET.acquire()` first.
- `USER_AGENT = f"apfun-funnel:v0.1 (by /u/{settings.reddit_username})"`. The username comes from `settings.reddit_username`, validated at `Settings()` construction — see "Config" below.

### Config (`reddit_username`, fail-loud)

- `apfun/config.py`: add `reddit_username: str` (env: `APFUN_REDDIT_USERNAME`).
- Custom Pydantic v2 validator (per feedback 011 Q1, mirroring the `host` validator pattern already in `apfun/config.py`):

  ```python
  @field_validator("reddit_username", mode="after")
  @classmethod
  def _validate_reddit_username(cls, v: str) -> str:
      if not v or not v.strip():
          raise ValueError(
              "APFUN_REDDIT_USERNAME is required. Reddit silently blocks "
              "non-conformant User-Agents — an empty username produces "
              "phantom-empty results, not errors. Set the env var to your "
              "Reddit handle. See CLAUDE.md → Networking for context."
          )
      return v.strip()
  ```

  Fail-loud at `Settings()` construction (module-scoped in `apfun/config.py`, so effectively import-time). The CLAUDE.md-pointing message is what turns a 6am runtime error into a 60-second resolution. Per feedback 008 Q2 / 011 Q1.

### Ingester/scheduler split (per feedback 011 Q2)

`ingest()` is per-source and dumb — captures rows, observes status codes, reports back. `ingest_batch(session, sources)` is the batch-aware wrapper — tallies status codes across `IngestResult`s, applies the UA-block guard, decides whether to increment `consecutive_failures`, and writes the `scheduler_runs` row. The UA-block test lives at the batch layer; the per-source test stays simple.

Result struct:

```python
@dataclass
class IngestResult:
    source_id: int
    items_captured: int
    status_codes: list[int]   # per-listing-call codes; batch layer inspects these
    error_class: str | None
    latency_ms: int
```

`status_codes` is a *list* because one source may issue multiple listing calls per ingest (pagination, multi-subreddit configs). `ingest_batch` flattens across results to compute the UA-block fraction.

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

- **Unit tests** mock `httpx.Client` against `tests/fixtures/reddit/listing_*.json` captures. Verify dedup, deletion-tagging, single-source `ingest()` behavior, UA format, rate-limit acquire calls.
- **Batch-wrapper unit test** (`tests/unit/test_reddit_ingest_batch.py`): mixed-result scenarios. Three-strikes counter increments on TERMINAL_STATUSES; transient errors (5xx, 429, timeout) don't increment; >50%-batch 403 triggers the UA-block guard and suppresses counter increments. This is where the batch logic gets exercised.
- **Schema contract test** (per the new convention from feedback 009): `tests/unit/test_reddit_schema_contract.py` asserts the fields the ingester depends on (`kind`, `data.children[].data.{id, subreddit, title, selftext, score, num_comments, created_utc, permalink, url}`). Fails loudly if a fixture refresh shows Reddit changed the response shape.
- **Integration test** (`tests/integration/test_reddit_live.py`, `@pytest.mark.integration`, gated on internet access) hits one real subreddit, asserts ≥1 row inserted, and asserts schema contract holds against the live response. **Doesn't write fixtures** — capture is a separate, explicit action.
- **Capture script** (per feedback 011 Q3): `scripts/capture_reddit_fixture.py`, mirroring `scripts/capture_response_fixture.py`. Hits specific endpoints, writes `tests/fixtures/reddit/listing_*.json` with `_fixture_meta` populated. Run explicitly when a new fixture is needed or a contract test fails. Two artifacts, two clearly-named entrypoints, one mental model.
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
- Integration test (opt-in) hits one real subreddit and inserts ≥1 row. (Fixture capture is the separate `scripts/capture_reddit_fixture.py`, not a side effect of the test.)
- Schema contract test green against a captured fixture in `tests/fixtures/reddit/` (capture via the script).
- Three consecutive 404s on a source disable it (`is_active=False`) via the batch wrapper; next successful fetch on a re-enabled source resets the counter.
- Batch-wrapper test: >50% 403s in one batch triggers UA-block guard (no per-source counter increments).
- `Settings()` construction without `APFUN_REDDIT_USERNAME` raises `ValidationError` with the CLAUDE.md-pointing message. With the env var set, settings construct cleanly.
- `grep -r '# TODO verify' apfun/` at task end returns zero matches, or each match is accompanied by an open escalation request (see Notes).

## Notes

- Reddit's public-JSON-no-auth endpoint is rate-limited (~10 QPM per IP). When we observe 429s with any frequency, that's the trigger to open an orchestrator request for OAuth migration — not a pre-emptive build (feedback 007 Q3).
- Captured fixtures may include user identifiers that look like PII (usernames, comment text). Treat the fixtures as you would the live data — they're committed to the repo but represent public content. If we ever need to scrub before commit, that's a separate concern.
- **`# TODO verify` resolution at task end** (per feedback 011 Q4): Reddit's docs are unstable enough that 1-2 constants are expected to land as `# TODO verify by end of task 005` rather than `# verified`. Top candidates: the unauth QPM ceiling and the UA format requirement. If those resolve only to community threads (not an authoritative Reddit URL), open a short escalation request listing each unresolved constant + best citation; the orchestrator will most likely re-annotate as `# heuristic` rather than have us keep hunting. Declaring values as judgment is better than fake authority.
