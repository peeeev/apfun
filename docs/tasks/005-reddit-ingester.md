# 005 — Reddit ingester

**Goal:** Pull recent posts from a configurable set of subreddits into `raw_signals`, deduped.

Depends on: 002.

## Deliverables
- `apfun/sourcing/reddit.py`: async function `ingest(session, source)` reads `source.config_json` for `{"subreddits": [...], "since_hours": 6, "fetch_kind": "new"|"top"}`.
- Fetches `https://www.reddit.com/r/<sub>/new.json?limit=100` (public JSON, no auth) via `httpx.AsyncClient` with a polite `User-Agent: apfun/0.1 (alex@apfun.online)`.
- Per-post: build `content_hash = sha256(subreddit + permalink + title + selftext)`, skip if already present in `raw_signals`.
- Inserts new rows with `payload_json` containing `{title, selftext, author, score, num_comments, permalink, created_utc, subreddit, flair}`.
- Updates `source.last_fetched_at` on success / `source.last_error` on failure.
- Bootstrap script `scripts/seed_sources.py` registers the brief's core subs (`SaaS`, `Entrepreneur`, `SmallBusiness`) + 10–20 vertical placeholders, marked `is_active=True`.

## Acceptance
- Fixture-backed unit test loads a saved JSON response and asserts:
  - N rows inserted on first run.
  - 0 rows inserted on a second run of the same fixture (dedup works).
- Integration test (network, marked `@pytest.mark.integration`, opt-in) hits one real subreddit and inserts ≥1 row.
- Rate-limit-friendly: max 1 req/sec across all subreddits in this run; configurable.

## Notes
- Public JSON endpoint is rate-limited (~100 req/10min per IP). If we hit 429s in practice, task 005b will add OAuth via a registered Reddit app — defer.
