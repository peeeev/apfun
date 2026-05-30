# 015 — DataForSEO client + budget guard

**Goal:** an HTTP client for DataForSEO with HTTP Basic Auth, monthly $25 budget cap, per-call audit, rate-limit awareness, and a `/ops` budget surface. Foundation only — no consumers yet (Stages 3/4 — tasks 016/017 — will import this).

**Complexity:** M

Depends on: 003 (telemetry tables). Per orchestrator request 033.

## Two endpoint families

- **`serp/google/organic/...`** — Stage 3 (task 016) competitive scrape: who currently ranks for a candidate's keywords. Defaults to **Standard Queue** mode (~5 min, $0.0006/query); `priority` and `live` overrideable per call.
- **`keywords_data/google_ads/search_volume/live`** — Stage 4 (task 017) saturation scoring: search volume, CPC, competition. **Live mode only** (single request; per-task flat pricing — queue mode would add latency for no per-call savings). $0.075/task (up to 1000 keywords/task).

Two corresponding high-level methods on `DataForSEOClient`:
- `serp_google_organic(keyword, *, location_code=2840, language_code="en", queue_mode=None, depth=10) -> SerpResult`
- `keywords_google_ads_search_volume(keywords, *, location_code=2840, language_code="en") -> KeywordVolumeResult`

## Deliverables

**Module** `apfun/clients/dataforseo.py`:
- `DataForSEOClient(settings, *, client=None, _session_factory=None)` — sync; one per process. Test seams for the httpx client (use `httpx.MockTransport`) and DB session factory.
- Loud-failure at construction if `APFUN_DATAFORSEO_LOGIN`/`APFUN_DATAFORSEO_PASSWORD` empty, with a pointer to use the **dedicated API password** (NOT account login — DataForSEO's #1 integration failure).
- Pydantic response schemas: `SerpResult`/`OrganicItem`, `KeywordVolumeResult`/`KeywordVolumeItem`. `extra="ignore"` so DataForSEO can add fields without breaking parsers.
- Exceptions: `DataForSEOError` (base), `DataForSEOBudgetExceededError`, `DataForSEOAccountSuspendedError` (40201), `DataForSEOAPIError` (other non-20000), `DataForSEOTaskTimeoutError`.

**Pricing constants** (with `# verified 2026-05-30` source-URL annotations per the verify-constants convention):
- `_SERP_COST_PER_QUERY_USD = {"standard": 0.0006, "priority": 0.0012, "live": 0.002}`
- `_KEYWORDS_GOOGLE_ADS_COST_PER_TASK_USD = 0.075`
- Depth multiplier: cost doubles per extra 100 results above depth=10.

**Schema** — new table `dataforseo_usage` (per-call audit + budget source):
- Columns: `family` (`serp`/`keywords_google_ads`), `endpoint`, `queue_mode`, `est_cost_usd`, `latency_ms`, `status_code`, `task_id`, `response_size_bytes`, `ok`, `error`, plus the `TimestampMixin` `created_at`/`updated_at`.
- Migration `d4e6f8a0b2c5` (new table, no children → no cascade risk).
- **Deviation from request 033 Q1** — the orchestrator's lean was to extend `api_usage`, but the existing `api_usage` is a *daily aggregate* (one row per provider/day), not per-call. Per-call fields (`task_id`, `queue_mode`, `latency`) don't fit the aggregate model, so the spec's Q1 option (b) was chosen: new dedicated table. `api_usage` stays untouched.

**Settings** (in `apfun/config.py`):
- `APFUN_DATAFORSEO_LOGIN`, `APFUN_DATAFORSEO_PASSWORD` — loud-failure at first call site.
- `APFUN_DATAFORSEO_BUDGET_USD_PER_MONTH` — default 25.0.
- `APFUN_DATAFORSEO_BASE_URL` — default `https://sandbox.dataforseo.com/v3/` (sandbox-first build pattern; operator flips to production after a green runbook 005). Validator normalizes a single trailing slash.
- `APFUN_DATAFORSEO_SERP_QUEUE_MODE` — `standard` (default) / `priority` / `live`. Overrideable per call.

**Budget guard**: pre-call, `SUM(est_cost_usd) FROM dataforseo_usage WHERE created_at >= start_of_month_utc`. If `spent + estimated > cap`, raise `DataForSEOBudgetExceededError`. Soft cap — actual recorded spend can slightly exceed the cap (depth multiplier edge cases); next call correctly aborts. Operator raises the env var + restarts to resume.

**Rate-limit awareness**:
- **Reactive general throttle**: read `X-RateLimit-Remaining` on every response; if < 100, sleep 2s before the next call.
- **Google Ads Live 12-req/min cap**: separate sliding-window deque per the per-account cap (verified at https://dataforseo.com/help-center/dataforseo-api-limits); 13th call within 60s sleeps until the oldest entry rolls out.

**Standard Queue async flow** (SERP only): `_task_post` → returns task_id → `_task_get` polls (5s → backoff to 30s, 10 min total timeout) until `status_code=20000`. task_post writes a cost-bearing audit row; each poll writes a $0 audit row (free).

**`/ops` budget surface** — new "DataForSEO budget" section in `_ops_body.html`:
- Current-month spend / cap / remaining + %.
- Split by family (serp vs keywords_google_ads): calls + spent.
- Last call: timestamp + ok/error + endpoint.
- "Not configured" placeholder when creds are empty (the pre-runbook-005 state).

## Acceptance

- Unit tests pass `make check` (20 tests in `test_dataforseo_client.py`): estimators, construction loud-failure, parse SERP + keyword fixtures, budget happy / over / cross-month, usage row recording, ok=False on API errors, 40201 → `DataForSEOAccountSuspendedError`, reactive rate-limit throttle, Google Ads 12/min throttle, Standard Queue task_post→task_get path, budget+recording integration.
- Synthetic fixtures at `tests/fixtures/dataforseo/serp_google_organic_synthetic.json` + `keywords_google_ads_search_volume_synthetic.json` — marked synthetic via `_fixture_meta` so runbook 005's real-capture step naturally replaces them.
- `/ops` renders the new section (empty/configured-or-not state both work).

## Out of scope

- Any Stage 2/3/4 consumer code (tasks 011, 016, 017).
- Other DataForSEO endpoints (Backlinks, Labs, Trends, OnPage, etc.) — add when consumers materialize.
- Caching SERP responses across days, query dedup.
- Webhook (`postback_url`) for Standard Queue — polling is fine at our scale.
- Daily cap (only monthly for v1).
- Real Sandbox/production fixture captures — those are runbook 005's deliverable.

## Schema deviation (recorded)

Request 033 §Q1 leaned toward extending `api_usage`. I overrode to option (b) — new `dataforseo_usage` table — because the existing `api_usage` is daily-aggregate-shaped, not per-call, and the per-call fields the spec requires (`task_id`, `queue_mode`, `latency_ms`) don't compose with that shape. Per the *verify referenced affordances* convention.

## Operator pre/post — runbook 005

The operator-side work (sign up, $50 deposit, generate the **dedicated API password**, smoke test against Sandbox, switch to production, eyeball cost recording) lives in `docs/operator/runbooks/005-dataforseo-first-pass.md`. Per request 033 §"What I would do next": **ship the PR first, defer runbook 005 to whenever the operator does the deposit.** Decouples code work from operator-time work.

Cost of runbook 005 itself: <$0.20.
