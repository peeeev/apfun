# Runbook 005 — DataForSEO first pass

**Purpose:** validate the DataForSEO client from this server, Sandbox first then production, and replace the synthetic fixtures with real captures. Empirical-gate for task 015.

**Budget:** <$0.20 total (5 sandbox calls free; 1 production SERP $0.0006 + 1 production keyword task $0.075 + headroom). The $50 minimum deposit is the real spend gate — runbook itself is trivial.

**Prerequisites:**
- Task 015 merged + deployed (`/workspace` on `main` with the `dataforseo_usage` table migrated).
- Operator has signed up at https://dataforseo.com.
- **$50 minimum deposit completed.**
- **Dedicated API password generated** at https://app.dataforseo.com/api-access (NOT the account login password — this is the #1 source of integration failures per DataForSEO's own guides).

## Step 0 — Set credentials, leave base URL on Sandbox

In `/srv/claude/apfun.online/.env` (host-side):

```
APFUN_DATAFORSEO_LOGIN=<your-registration-email>
APFUN_DATAFORSEO_PASSWORD=<dedicated-api-password-from-app-dataforseo-com>
# Leave the default — sandbox first.
# APFUN_DATAFORSEO_BASE_URL=https://sandbox.dataforseo.com/v3/
```

Restart the container or let `--reload` pick up changes (a non-`.py` change to `.env` may NOT trigger reload — `docker compose -f /srv/claude/apfun.online/docker-compose.yml restart` is safest).

## Step 1 — Verify credentials load

```bash
docker exec -it apfun-funnel uv run python -c \
  "from apfun.config import Settings; s = Settings(); print('login set:', bool(s.dataforseo_login), '/ password set:', bool(s.dataforseo_password), '/ base_url:', s.dataforseo_base_url)"
```

Expect: `login set: True / password set: True / base_url: https://sandbox.dataforseo.com/v3/`.

## Step 2 — Sandbox: one SERP call

```bash
docker exec -it apfun-funnel uv run python -c "
from apfun.clients.dataforseo import DataForSEOClient
c = DataForSEOClient()
r = c.serp_google_organic('site reliability engineer notes', queue_mode='live')
print('keyword:', r.keyword)
print('items:', len(r.items))
for it in r.items[:3]:
    print('  ', it.rank_absolute, it.domain, '-', it.title)
"
```

Expect: parsed `SerpResult` with ≥1 item. Sandbox returns simulated/canned data; the shape is what matters.

Verify the audit row:

```bash
docker exec -it apfun-funnel sqlite3 /workspace/data/apfun.db \
  "SELECT family, endpoint, queue_mode, est_cost_usd, ok FROM dataforseo_usage ORDER BY id DESC LIMIT 1"
```

Expect: `serp|serp/google/organic/live/advanced|live|0.002|1`.

## Step 3 — Sandbox: one Google Ads keyword call

```bash
docker exec -it apfun-funnel uv run python -c "
from apfun.clients.dataforseo import DataForSEOClient
c = DataForSEOClient()
r = c.keywords_google_ads_search_volume(['note taking app', 'obsidian alternative'])
for i in r.items:
    print(f'{i.keyword!r}: vol={i.search_volume} comp={i.competition}/{i.competition_index} cpc=\${i.cpc}')
"
```

Expect: parsed `KeywordVolumeResult` with N items matching N input keywords. Sandbox CPC/competition values may be synthetic but populated.

Verify the audit row (family=`keywords_google_ads`, est_cost_usd=0.075).

## Step 4 — Capture real fixtures from Sandbox

Replace the synthetic fixtures with the real Sandbox responses (they share the production shape):

```bash
docker exec -it apfun-funnel uv run python -c "
import json, httpx
from apfun.config import settings as s
c = httpx.Client(base_url=s.dataforseo_base_url, auth=(s.dataforseo_login, s.dataforseo_password), timeout=60)
serp = c.post('serp/google/organic/live/advanced', json=[{'keyword':'site reliability engineer notes','location_code':2840,'language_code':'en','depth':10}]).json()
kw = c.post('keywords_data/google_ads/search_volume/live', json=[{'keywords':['note taking app','obsidian alternative'],'location_code':2840,'language_code':'en'}]).json()
print(json.dumps(serp, indent=2)[:500]); print('---'); print(json.dumps(kw, indent=2)[:500])
" > /tmp/dataforseo-captures.json
```

If the captures look right, commit them as `tests/fixtures/dataforseo/serp_google_organic_real.json` and `keywords_google_ads_search_volume_real.json` (drop the `_fixture_meta` header). Keep the synthetic copies as the value-asserting fixtures; the real captures back the schema-contract tests (per the *fixtures serve two different jobs* lesson).

## Step 5 — Switch to production

In the host `.env`:

```
APFUN_DATAFORSEO_BASE_URL=https://api.dataforseo.com/v3/
```

Restart the container. Re-run step 1 to confirm `base_url` flipped.

## Step 6 — Production: one SERP call

Same command as step 2. Expect: parsed result, $0.002 audit row.

**This call costs $0.002 of real money. Verify it appears in DataForSEO's billing dashboard within ~1 min.**

## Step 7 — Production: one Google Ads keyword call

Pick ~5 keywords from an approved candidate in `/inbox` (real data with real intent). Run step 3 with those keywords. Expect: real CPC + competition values, $0.075 audit row.

## Step 8 — Confirm `/ops` budget surface

Open `/ops` in a browser. The "DataForSEO budget" section should show:
- ~$0.077 spent ($0.002 SERP + $0.075 keyword) of $25.00.
- Two rows: `serp` (1 call, $0.002) and `keywords_google_ads` (1 call, $0.075).
- Last call: ✓ keywords_google_ads · just now.

If `/ops` still says "Not configured", confirm `APFUN_DATAFORSEO_LOGIN` is set in the container's env (`docker exec apfun-funnel env | grep DATAFORSEO`).

## Step 9 — Report back

Send the orchestrator:
1. ✓ / ✗ on each step.
2. Actual response times (Sandbox vs prod).
3. Did costs match estimates ($0.002 SERP / $0.075 keyword)?
4. Anything surprising — schema fields not in our Pydantic models, unexpected rate-limit behavior, 40201 / 40202 errors, etc.

Expected gotchas per request 033:
- Dedicated-API-password confusion (already addressed in step 0).
- Sandbox vs production shape drift — should be identical per DataForSEO's docs.
- Standard Queue 5-min latency feels slow on first try; that's why apfun defaults to Live for keyword data.
