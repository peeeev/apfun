# 006 — Hacker News ingester

**Goal:** Pull HN posts and comments matching opportunity-revealing patterns ("Ask HN: what tool…", "show me a SaaS that…", "I wish there were…") into `raw_signals`.

**Complexity:** S

Depends on: 002.

## Deliverables
- `apfun/sourcing/hn.py` using the Algolia HN search API (`https://hn.algolia.com/api/v1/search_by_date`).
- `source.config_json`: `{"queries": ["tool you wish existed", "what software is missing", "alternatives to", ...], "since_hours": 6}`.
- Per query, page through results since the configured window. Capture both posts and matching comments.
- Content hash = `sha256(objectID)`. `payload_json` includes title, story_text, author, points, num_comments, created_at, url, objectID, type.
- Polite delay between requests (~250ms).

## Acceptance
- Fixture test: load saved Algolia response, assert dedup behavior and row contents.
- Integration test (opt-in): fetches a small real window, inserts at least one row.

## Notes
- The Algolia API is unauthenticated and generous. Don't add OAuth.
- Filter out posts with `points < 3` and comments with `points < 1` to reduce noise. Make the threshold configurable.
