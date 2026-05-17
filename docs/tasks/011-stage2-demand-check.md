# 011 — Stage 2 demand check

**Goal:** Cheap kill/keep filter on `candidates` using Google Trends + autosuggest. Survivors graduate to `pending_review` (the HITL inbox).

Depends on: 010.

## Deliverables
- Deps: `pytrends`, `httpx`.
- `apfun/demand/trends.py`: thin async wrapper over `pytrends` (sync lib — run in threadpool). Computes trend slope over the last 12 months using `interest_over_time`.
- `apfun/demand/autosuggest.py`: scrapes `https://suggestqueries.google.com/complete/search?client=firefox&q=<seed>` for related queries.
- `apfun/demand/check.py`:
  - For each candidate with `status=pending_demand`: take top 1–3 seed keywords, compute trend slope, fetch autosuggest, write a `demand_checks` row.
  - Verdict rules (configurable, defaults):
    - `fail` if all keyword slopes are < -0.2 AND no autosuggest matches contain "alternative to" / "vs" / "best" patterns → set candidate `status=auto_killed`.
    - `pass` otherwise → set candidate `status=pending_review`.
  - Always persist the underlying slope + raw autosuggest into `demand_checks.autosuggest_json` so the threshold can be retuned later.
- Rate-limit: max 1 trends call / 5 sec; respect pytrends' built-in backoff.

## Acceptance
- Unit test with stubbed trends/autosuggest fixtures verifies kill and survive paths.
- Integration test runs on 3 candidates against the real Trends API.
- A killed candidate retains its `demand_checks` row (we want to retune the threshold against ground truth later).

## Notes
- Trends has hard rate limits and can soft-ban an IP. Cache results per (keyword, day). If we get banned, fall back to `glimpse` (paid) — defer.
- "Saturated ≠ skip." High volume + flat slope is not a kill; only flat-and-no-alternative-signal is. See brief §10.
