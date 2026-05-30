# Request 033: task 015 — DataForSEO client + budget guard

**Date:** 2026-05-30

**Context.** Phase C kickoff. Stages 2-5 turn approved candidates into scored, differentiated opportunities. Stage 3 (competitive scrape) is the paid-API stage and DataForSEO is its provider. Per the convention that paid-API clients precede their consumers (LLM wrapper preceding Stage 1, this task preceding Stages 2/3), task 015 builds the client first, then runbook 005 validates it from this server, then Stage 2 (011) and Stage 3 (016) consume it.

**Pre-spec discovery** (web-searched 2026-05-30, per the verify-before-spec discipline from the Reddit incident):

- **SERP API pricing**: Standard Queue $0.0006/query (~5min), Priority $0.0012/query (~1min), Live $0.002/query (real-time).
- **Google Ads Keyword Data API pricing**: ~$0.075/task (one task can carry up to 1000 keywords; cost is per-task, not per-keyword).
- **Deposit / trial**: $50 minimum deposit, $1 trial credit on signup.
- **Auth**: HTTP Basic Auth with email + **dedicated API password** (separate from account login password). This is the #1 source of integration failures per multiple integration guides — emphasize in operator setup docs.
- **API**: v3 at `https://api.dataforseo.com/v3/`. Sandbox env at `https://sandbox.dataforseo.com/v3/` with identical response shape (free, simulated responses).
- **Rate limits**: 2000 req/min general; Google Ads Live endpoints capped at 12 req/min per account; 30 concurrent for live SERP/OnPage. `X-RateLimit-Limit` + `X-RateLimit-Remaining` headers on every response.
- **Standard Queue uses task_post → task_get async pattern**; Live mode is single-request.

**Provider-comparison summary** (rationale for choosing DataForSEO over alternatives — full analysis in feedback turn 033-discussion):

- SEMrush: API access gated behind $549/mo Business tier; agency-scale pricing. Overkill for personal-use volume.
- SE Ranking: $88/mo Starter tier. Cheaper than SEMrush but still subscription-floored.
- Google Ads API directly: free for keyword data but requires active campaign with real ad spend for precise (non-range) data, OAuth setup, ongoing campaign maintenance, separate developer token application. Operational cost outweighs ~$15/mo savings for personal scale.
- Moz: $5/mo but no CPC/competition data — wrong shape.
- Ahrefs: $999+/mo. Out of range.
- DataForSEO: $50 deposit + pay-per-use. Powers a large fraction of the SEO tool ecosystem under the hood; precise data at small-operator pricing.

**Cost math for apfun's expected scale:**

Stage 3 + Stage 4 use both endpoint families per candidate:

- ~50 candidates/week reaching Stage 3 (post-Stage-2 filter)
- SERP queries (one per candidate): 50 × $0.0006 = $0.03/week ≈ $0.13/month
- Google Ads keyword data tasks (one per candidate, batches all keywords): 50 × $0.075 = $3.75/week ≈ $15/month
- **Combined Stage 3 + Stage 4 cost: ~$15/month at full throughput**

The original brief's $25/month cap allows ~60% utilization at full throughput, leaving real headroom for traffic spikes and exploration queries. **Recommendation: set budget cap at $25/month per brief.**

## Goal

A DataForSEO HTTP client with:

1. HTTP Basic Auth via env vars (loud-failure pattern).
2. Two endpoint families exposed:
   - **SERP API** (`serp/google/organic/...`) — for Stage 3 competitive scrape (who currently ranks for keywords).
   - **Google Ads Keyword Data API** (`keywords_data/google_ads/search_volume/...`) — for Stage 4 saturation scoring (CPC, competition score, search volume from Google Keyword Planner).
3. Standard Queue mode as default for SERP (cheapest, suits our non-realtime use case). Google Ads keyword endpoints use Live mode (per-task pricing dominates; queue mode would be marginal savings at our scale).
4. **Monthly budget cap** (default $25/mo, operator-configurable). Pre-call check aborts requests that would exceed.
5. Cost tracking persisted to a new column on the existing `api_usage` table (or sibling — see Q1).
6. X-RateLimit-Remaining-aware rate limiting (slow down before hitting limits).
7. Schema contract tests against Sandbox-captured fixtures for each endpoint family.
8. /ops dashboard extension showing DataForSEO budget remaining.

This is a foundation task — no consumers yet. Stage 2 (task 011) uses pytrends; Stage 3 (task 016) imports the SERP method; Stage 4 (task 017) imports the Google Ads keyword method.

## Scope

**In scope:**

- `apfun/clients/dataforseo.py` — new module. HTTP client wrapping httpx with auth, rate limiting, cost tracking, budget guard.
- Settings additions:
  - `APFUN_DATAFORSEO_LOGIN` (email) — fail-loud at first call
  - `APFUN_DATAFORSEO_PASSWORD` (dedicated API password, NOT account login) — fail-loud at first call
  - `APFUN_DATAFORSEO_BUDGET_USD_PER_MONTH` (default `25.0`) — soft cap, halts new requests when crossed
  - `APFUN_DATAFORSEO_BASE_URL` (default `https://sandbox.dataforseo.com/v3/`) — sandbox-first build pattern; operator switches to `https://api.dataforseo.com/v3/` after smoke test
  - `APFUN_DATAFORSEO_SERP_QUEUE_MODE` (default `standard`, enum: `standard | priority | live`) — almost always `standard` for apfun's use case
- **Two high-level methods** corresponding to the two endpoint families:
  - `client.serp_google_organic(keyword: str, location_code: int = 2840, language_code: str = "en") -> SerpResult` — Stage 3's primary call. Returns parsed Pydantic model with organic results, ads, related queries, etc. Uses configured queue mode (default Standard, async).
  - `client.keywords_google_ads_search_volume(keywords: list[str], location_code: int = 2840, language_code: str = "en") -> KeywordVolumeResult` — Stage 4's primary call. Returns per-keyword `avg_monthly_searches`, `competition_level` (LOW/MEDIUM/HIGH), `competition_index` (0-100), `low_top_of_page_bid_usd`, `high_top_of_page_bid_usd`. Uses Live mode (single request, fast; per-task pricing dominates).
- **Two lower-level methods** covering the Standard Queue async flow (for SERP):
  - `client._task_post(endpoint: str, payload: dict) -> str` — returns task ID
  - `client._task_get(endpoint: str, task_id: str) -> dict` — polls until result available
- **Budget guard**: pre-call, check `SUM(cost_usd) FROM api_usage WHERE service='dataforseo' AND created_at >= start_of_month_utc`. If `sum + estimated_cost > budget_cap`, raise `DataForSEOBudgetExceededError`. Loud-failure pattern; operator must explicitly increase the cap (env var change + container restart) to resume.
- **Cost tracking**: every successful request writes a row to `api_usage` with service='dataforseo', endpoint, cost_usd, status_code, response_size_bytes, task_id (nullable), latency_ms, queue_mode.
- **Rate limit awareness**: read X-RateLimit-Remaining on each response; if below threshold (suggest: 100), sleep before next request. Avoid hitting 40202 ("rate-limit per minute exceeded") errors proactively. **Special-case the Google Ads Live cap of 12 req/min** — separate counter for keyword endpoints with appropriate throttling.
- **Schema contract tests** against Sandbox-captured fixtures (one per endpoint family). Same pattern as Reddit / HN fixtures: synthetic for value-asserting tests, real-captured for schema-contract.
- /ops dashboard new section: "DataForSEO budget" showing current-month spend ($X.XX of $25.00), spend breakdown by endpoint family (SERP vs Google Ads keyword), last-call timestamp, last-call status.

**Out of scope:**

- Any actual Stage 2, 3, 4 consumer code. That's tasks 011, 016, 017. This PR delivers the client; consumers come later.
- Other DataForSEO endpoints (Backlinks API, Labs API, etc.). Only `serp/google/organic` and `keywords_data/google_ads/search_volume` for v1. Other endpoints add when consumers materialize.
- Google Trends endpoint (DataForSEO's wrapper around Google Trends). Stage 2 will use pytrends first; if pytrends proves unreliable, fall back to DataForSEO's Trends API in a future task. Don't pre-build.
- Cost optimization (caching SERP responses across days, query-deduplication). Stage 3's volume is small (~50/week) — caching adds complexity for negligible savings. Defer.
- Priority/Live mode optimization for SERP. Standard is the default; operators can override per-call via parameter, but no automatic mode selection.

## Implementation shape

In `apfun/config.py`:

```python
class Settings(BaseSettings):
    # ... existing ...
    dataforseo_login: str = ""
    dataforseo_password: str = ""
    dataforseo_budget_usd_per_month: float = 25.0
    dataforseo_base_url: str = "https://sandbox.dataforseo.com/v3/"
    dataforseo_serp_queue_mode: Literal["standard", "priority", "live"] = "standard"

    @field_validator("dataforseo_base_url", mode="after")
    @classmethod
    def _normalize_dataforseo_url(cls, v: str) -> str:
        return v.rstrip("/") + "/"
```

No fail-loud validator at `Settings()` for `login` and `password` (per the auth-secret discipline from feedback 013). Fail-loud at first client call site:

```python
class DataForSEOClient:
    def __init__(self, settings: Settings):
        if not settings.dataforseo_login or not settings.dataforseo_password:
            raise RuntimeError(
                "APFUN_DATAFORSEO_LOGIN and APFUN_DATAFORSEO_PASSWORD required. "
                "The password is your DEDICATED API PASSWORD from "
                "https://app.dataforseo.com/api-access, NOT your account login password. "
                "Using the wrong password is the #1 setup failure for this API."
            )
        self._settings = settings
        self._client = httpx.Client(
            base_url=settings.dataforseo_base_url,
            auth=(settings.dataforseo_login, settings.dataforseo_password),
            timeout=httpx.Timeout(120.0),  # per DataForSEO best practice for SERP
        )
        self._rate_remaining = 2000  # optimistic init; updated from headers
```

Budget guard:

```python
def _check_budget(self, estimated_cost_usd: float) -> None:
    with SessionLocal() as s:
        start_of_month = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        spent = s.scalar(
            select(func.coalesce(func.sum(ApiUsage.cost_usd), 0.0))
            .where(ApiUsage.service == "dataforseo")
            .where(ApiUsage.created_at >= start_of_month)
        )
        if spent + estimated_cost_usd > self._settings.dataforseo_budget_usd_per_month:
            raise DataForSEOBudgetExceededError(
                f"Budget cap ${self._settings.dataforseo_budget_usd_per_month:.2f} would be "
                f"exceeded: ${spent:.4f} spent this month, "
                f"${estimated_cost_usd:.4f} requested. Increase "
                f"APFUN_DATAFORSEO_BUDGET_USD_PER_MONTH and restart to resume."
            )
```

Rate-limit awareness (simple — not predictive, just reactive):

```python
def _maybe_throttle(self) -> None:
    if self._rate_remaining < 100:
        # Approaching limit. Wait until the next minute window.
        time.sleep(2.0)  # crude but safe; refines if we ever actually hit limits

def _record_rate_headers(self, response: httpx.Response) -> None:
    remaining = response.headers.get("X-RateLimit-Remaining")
    if remaining is not None:
        try:
            self._rate_remaining = int(remaining)
        except ValueError:
            pass
```

The pricing computation:

```python
# SERP pricing — verified 2026-05-30 https://dataforseo.com/apis/serp-api/pricing
_SERP_COST_PER_QUERY_USD = {
    "standard": 0.0006,
    "priority": 0.0012,
    "live": 0.002,
}

# Google Ads Keyword Data pricing — verified 2026-05-30
# https://dataforseo.com/pricing/keywords-data/google-ads
# Per-task pricing regardless of keyword count (up to 1000 keywords/task)
_KEYWORDS_GOOGLE_ADS_COST_PER_TASK_USD = 0.075

def _estimate_serp_cost(self, mode: str, depth: int = 10) -> float:
    base = _SERP_COST_PER_QUERY_USD[mode]
    # Depth above default 10 doubles cost per extra 100 results.
    extra_hundreds = max(0, (depth - 10 + 99) // 100)
    return base * (2 ** extra_hundreds)

def _estimate_keywords_google_ads_cost(self, num_keywords: int) -> float:
    # Per-task pricing; no multiplier for keyword count up to 1000.
    if num_keywords > 1000:
        raise ValueError(
            f"Google Ads keyword endpoint accepts max 1000 keywords/task; got {num_keywords}"
        )
    return _KEYWORDS_GOOGLE_ADS_COST_PER_TASK_USD
```

## Q1 — Storage: extend `api_usage` or new table?

Current state per feedback 003 (telemetry tables): `api_usage` exists with columns `service, endpoint, cost_usd, status_code, created_at` (approx). Two options:

**(a) Extend `api_usage`** — add columns for DataForSEO-specifics like `task_id`, `queue_mode`, `latency_ms`, `response_size_bytes`. Existing rows have nulls for these.

**(b) New table `dataforseo_usage`** — DataForSEO-specific schema. Cleaner per-table; some duplication with `api_usage`.

**My lean: (a).** Extending `api_usage` follows the existing pattern (it's already serving multiple services); the new columns are mostly nullable on existing rows. Single source of truth for "what API spending has happened." Implementer can flip to (b) if the column count gets unwieldy.

## Q2 — Sandbox-first build vs production credentials at start

Strong recommendation: **sandbox-first**.

The pattern:

1. PR ships with `APFUN_DATAFORSEO_BASE_URL=https://sandbox.dataforseo.com/v3/` as the *default* in `.env.example`
2. Operator gets credentials, deposits $50 to production account, sets the env vars
3. Initial smoke test in runbook 005 runs against Sandbox first (free, validates schema parsing + auth shape)
4. Operator switches to production URL only after Sandbox smoke test passes
5. The very first production call is in runbook 005 step N — operator watches it complete, verifies cost was recorded correctly, then proceeds

This means the client must not assume which base URL is production vs sandbox. Both are functionally identical; only the URL changes.

## Tests

- Schema validation: parse both Sandbox-captured fixtures (SERP + keyword data); verify all Pydantic models construct successfully.
- Budget guard test: seed `api_usage` with $24.50 in dataforseo spend this month; assert next $0.50 call raises `DataForSEOBudgetExceededError`.
- Budget guard test: seed $24.99; small SERP request ($0.0006) succeeds (under cap).
- Budget guard test: seed $24.95; keyword data request ($0.075) raises `DataForSEOBudgetExceededError`.
- Budget guard reset test: seed $25 in spend from *last* month; current-month request succeeds.
- Rate limit reactive test: mock `X-RateLimit-Remaining=50`; assert `_maybe_throttle` introduces sleep before next call.
- **Google Ads endpoint throttle test**: mock 11 rapid keyword calls within 60 seconds; assert 12th call sleeps appropriately to stay under the 12 req/min cap.
- Auth failure test: with empty credentials, first call raises `RuntimeError` with the dedicated-password warning.
- Cost recording test: successful SERP call writes row to `api_usage` with correct service, cost, endpoint.
- Cost recording test: successful keyword data call writes row with `endpoint='keywords_data/google_ads/search_volume'`, correct cost.
- Contract test: live fixtures validate against Pydantic schemas; catches Sandbox→Production shape drift on either endpoint.
- Keyword validation: pass `num_keywords > 1000` to `keywords_google_ads_search_volume()` — raises `ValueError` pre-call.

Integration tests (gated on credentials present): run against Sandbox; assert ≥1 organic result returned by SERP call; assert ≥1 keyword has expected fields returned by keyword data call. Skipped in CI; runs locally and during runbook 005.

## Documentation updates (same PR)

1. **`docs/tasks/015-dataforseo-client.md`** — new task file with this spec.
2. **`docs/operator/SETUP.md`** — DataForSEO setup steps:
   - Sign up at https://dataforseo.com
   - Deposit $50 (required minimum)
   - At https://app.dataforseo.com/api-access, generate a **dedicated API password** (NOT your account login password — this is the #1 source of auth failures)
   - Verify the Keyword Data API is enabled on the account (it ships enabled by default but worth confirming under the "APIs" tab)
   - Set `APFUN_DATAFORSEO_LOGIN` (your registration email) and `APFUN_DATAFORSEO_PASSWORD` (dedicated API password) in workspace/.env
   - Leave `APFUN_DATAFORSEO_BASE_URL` at sandbox default for the initial smoke test
   - Restart container or wait for `--reload` to pick up changes
   - Run runbook 005
3. **`docs/operator/runbooks/005-dataforseo-first-pass.md`** — to be written as part of this PR's deliverables. Steps:
   - Verify credentials load (`uv run python -c "from apfun.config import Settings; print(bool(Settings().dataforseo_login))"`)
   - Sandbox: run a single SERP call manually; verify response parses
   - Sandbox: run a single Google Ads keyword search_volume call manually; verify response parses
   - Verify cost rows written to `api_usage` for both
   - Switch base URL to production
   - Production: run one SERP call; verify parse + cost row
   - Production: run one Google Ads keyword call (with ~5 sample keywords from an approved candidate); verify parse + CPC/competition data + cost row
   - Verify production cost matches estimates (~$0.0006 SERP, ~$0.075 keyword data)
   - Open `/ops` dashboard; confirm DataForSEO section shows budget data split by endpoint family
4. **`docs/tasks/000-overview.md`** — mark 015 in-progress, note 011/016 dependencies.
5. **`docs/orchestrator/INDEX.md`** — row 033 → answered after PR merges.
6. **`CLAUDE.md → Lessons Learned`** — no new entry from this task; the verify-before-spec discipline (now applied) is already documented.

## What I would do next without intervention

1. Snapshot DB: `bash scripts/db_snapshot.sh` (per backup discipline; the `api_usage` extension is a real migration).
2. Branch `feature/task-015-dataforseo-client`.
3. Alembic migration for `api_usage` extension (new nullable columns). Data-preservation tested per the discipline.
4. Implement `apfun/clients/dataforseo.py` per the sketch above.
5. Capture a Sandbox fixture (requires operator's credentials briefly, or use the public Sandbox without auth if available — verify during implementation).
6. Write tests including the budget guard and rate-limit reactions.
7. Extend `/ops` dashboard with the DataForSEO budget section.
8. Write runbook 005 file (operator executes post-merge).
9. Apply documentation updates.
10. Open PR. Note: "Foundation only — no consumer code. Tasks 011 and 016 will import this client. Runbook 005 validates end-to-end from this server."

## Specific questions or risks

1. **Operator gets credentials when?** This task's PR can ship without operator-provided credentials (the code is general-purpose; tests use stubs). But runbook 005 requires real credentials and a real $50 deposit. Two paths:
   - Operator deposits before PR ships → runbook 005 runs immediately after merge
   - Operator deposits later → PR ships, runbook 005 deferred until ready
   - **My lean: ship the PR first, defer runbook 005 to whenever the operator does the deposit.** Decouples the code work from the operator-time work.

2. **Sandbox vs production schema drift.** DataForSEO claims the response shape is identical across both endpoint families. The schema-contract tests guard against drift on production. If Sandbox-captured fixtures pass but production responses fail validation, that's a real surprise — surface in runbook 005.

3. **Standard Queue's async flow complicates the SERP client.** Standard mode requires `task_post` → wait → `task_get`. The naive implementation polls with sleep; better implementations use webhook callbacks (DataForSEO supports `postback_url`). For v1, **polling with backoff is fine** — simpler, no public webhook endpoint needed. Webhooks are a future optimization if polling latency bothers anyone (unlikely at our scale). **Note:** keyword data endpoints use Live mode (single request) and don't have this concern.

4. **Google Ads Live 12 req/min cap is the real rate-limit gotcha.** General DataForSEO rate limit is 2000 req/min but Google Ads Live endpoints are capped much lower. At our expected scale (50 candidate-keyword calls per week ≈ 0.07/minute average) we're orders of magnitude below this, but the throttle logic should still respect the per-endpoint cap to avoid bursting on backfills or recoveries.

5. **What if DataForSEO blocks us for non-payment / verification / etc.?** Per their error code 40201 ("unusual activity, account paused"), they sometimes pause accounts. Handle as a loud failure — log the error, surface in /ops as "DataForSEO: account suspended (40201) — contact support@dataforseo.com." Don't retry into a suspended account.

6. **Budget cap timing edge case.** If spend = $24.99 and a $0.075 keyword call goes out, the check fails ($25.065 > $25 cap), call aborted. If spend = $24.50 and same call goes out, succeeds. If actual cost differs from estimate (unlikely for keyword data which is flat-rate, possible for SERP with depth multipliers), recorded spend can slightly exceed cap. Next call correctly aborts. **Acceptable: the cap is a *guard*, not a hard ceiling.**

7. **Daily snapshot of budget vs monthly?** Some operators prefer daily caps to catch runaway spending faster. **My lean: monthly only for v1.** At apfun's scale, daily granularity is unnecessary. Add later if a runaway-spend incident surfaces.

8. **Pre-existing api_usage rows from earlier services** — what services already write to `api_usage`? Worth checking before extending. If Anthropic LLM calls write there, the column extensions don't affect their writes (new columns nullable). If `api_usage` is unused so far, this is its first real consumer.

9. **Should SERP use Live mode if Standard Queue's 5-minute latency proves problematic?** Stage 3 batches don't need sub-second turnaround, so Standard is right. But if operator empirical observation shows the polling makes the funnel feel sluggish, switching to Priority ($0.0012/SERP, 2x cost) is a one-config-change escalation. Don't pre-optimize.

## Relevant files

Code under change:
- `apfun/clients/dataforseo.py` — new
- `apfun/config.py` — new settings
- `apfun/models/api_usage.py` — column additions
- `migrations/versions/NNN_extend_api_usage.py` — new migration
- `tests/unit/test_dataforseo_client.py` — new
- `tests/contract/test_dataforseo_schema.py` — new
- `tests/fixtures/dataforseo/serp_google_organic_sandbox.json` — new
- `tests/fixtures/dataforseo/keywords_google_ads_search_volume_sandbox.json` — new
- `apfun/web/routes/ops.py` — DataForSEO budget section
- `apfun/web/templates/ops.html` — UI for the new section

Docs:
- `docs/tasks/015-dataforseo-client.md` — new
- `docs/operator/SETUP.md` — DataForSEO setup
- `docs/operator/runbooks/005-dataforseo-first-pass.md` — new
- `docs/tasks/000-overview.md` — status update
- `docs/orchestrator/INDEX.md` — row 033 → answered

## Empirical validation (runbook 005)

Post-PR-merge, operator runs runbook 005:

1. Verifies credentials are loaded
2. Single Sandbox SERP call, verifies parse + cost record
3. Single Sandbox keyword data call, verifies parse + cost record
4. Switches to production base URL
5. Single production SERP call, verifies parse + cost record + real-data shape vs Sandbox
6. Single production keyword data call (with ~5 sample keywords from an approved candidate), verifies parse + CPC/competition fields populated + cost record
7. Opens /ops, confirms DataForSEO budget section displays correctly with split by endpoint family
8. Reports back: did it work, any surprises, what's the actual response time / size, did costs match estimates

Budget for the runbook itself: **<$0.20**. Five SERP calls × $0.0006 + two keyword calls × $0.075 = $0.153. Trivially under any concerning threshold.

## Meta note — Phase C entry

This is the first task in Phase C. The pattern shift to watch for: **less novelty in design decisions, more empirical surprises in third-party APIs.** Stage 1 had design surprises (HITL durability, cluster prompts, buildability layering); Phase C tasks are mostly "implement what the brief specified" with less open design space. But each runbook will surface 2-4 production-data quirks (rate limit reality vs documented, response shape edge cases, latency variance) per the empirical-discovery pattern from Stage 1.

Plan operationally: budget 1-2 post-runbook bug-fix orchestrator turns per Phase C stage. That's the cost of empirical validation against external systems.

The funnel finally produces *opportunities* (not just clustered candidates) after Stage 5 ships. That's the original brief's destination. Phase C is the path.
