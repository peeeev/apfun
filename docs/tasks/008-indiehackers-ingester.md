# 008 — IndieHackers ingester

**Goal:** Pull recent IH posts where people describe pains, failed tools, or "I built X" stories.

**Complexity:** M

Depends on: 002.

## Deliverables
- `apfun/sourcing/indiehackers.py` — IH has no official API. Use the public `https://www.indiehackers.com/grouppage/<group>` endpoints, falling back to HTML scraping with `selectolax` if JSON isn't exposed.
- `source.config_json`: `{"groups": ["main", "starting-up", "ideas-and-validation", ...], "since_hours": 24}`.
- Content hash on post URL.

## Acceptance
- Fixture test against saved HTML/JSON.
- Integration test (opt-in) fetches a small window successfully.
- Robust to layout changes: on parse failure, write a `scheduler_runs` row with `ok=false` and the error, do not raise.

## Notes
- If IH actively blocks scraping (Cloudflare challenge), park this source and re-prioritize task 009 (review mining).
