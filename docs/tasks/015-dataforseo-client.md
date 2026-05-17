# 015 — DataForSEO client + budget guard

**Goal:** A thin sync wrapper around DataForSEO's REST API with a hard monthly cost cap.

**Complexity:** M

Depends on: 003.

## Deliverables
- `apfun/sourcing/dataforseo.py`:
  - `class DataForSEOClient` taking `login`/`password` from env (`APFUN_DFS_LOGIN`, `APFUN_DFS_PASSWORD`).
  - Methods we'll need this month: `keyword_volume(keywords) -> {kw: volume}`, `keyword_difficulty(keywords)`, `cpc(keywords)`, `serp_top10(query, country, lang)`, `related_keywords(seed)` for "alternatives to X".
  - Every call records `api_usage(provider="dataforseo", day, est_cost_usd += this_call_cost, calls += 1, payload_json)`.
- Budget guard: before each call, sum `api_usage.est_cost_usd` for the current calendar month; if ≥ `APFUN_DFS_MONTHLY_CAP_USD` (default 25.0), raise `BudgetExceeded`. Pipeline callers (task 019) handle this by setting `pipeline_stage='failed'` with `reason='budget_blocked'` in the `pipeline_runs` row for the failed phase; a monthly retry job re-queues such candidates after rollover.
- Cost-estimate table maintained at the top of the module; updated when DFS changes prices.

## Acceptance
- Unit test with `respx`: a call records a row in `api_usage` with the right delta; second call when cap is reached raises `BudgetExceeded`; no network request is made when blocked.
- `scripts/dfs_usage.py` prints month-to-date spend.

## Notes
- DFS endpoints are POST. Don't accidentally fire sandbox vs. live endpoints — sandbox is free but returns canned data. Default to live; opt-in to sandbox via env for tests.
- Cap is a hard stop, not soft. Better to leave a candidate un-analyzed than blow the month.
