# Runbook 003 — Reddit residential-proxy first-pass

**Goal:** confirm whether Reddit-via-residential-proxy-with-browser-UA actually works from this datacenter server. Empirical question per orchestrator request 021 §Empirical validation. This is the test that decides whether Reddit goes back into the source rotation or gets disabled permanently.

**Background:** task 005c reverted the abandoned OAuth approach (Reddit closed self-service OAuth in Nov 2025) and pivoted to the anonymous public-JSON path through a residential proxy + browser-mimicking UA pool. Two independent blocks had to be addressed: datacenter-IP network block (→ proxy) and June-2025 web-frontend UA filtering (→ browser UAs).

**Budget guard:** $5 maximum. On Webshare's free tier the test costs nothing; on the $3.50/mo paid tier that's the whole month.

**Prerequisites:** task 005c merged + container restarted + `APFUN_REDDIT_HTTP_PROXY` set per `docs/operator/SETUP.md` → Reddit access.

---

## Step 0 — env sanity

```bash
docker exec -it apfun-funnel bash
cd /workspace
[ -n "${APFUN_REDDIT_HTTP_PROXY}" ] && echo "proxy: set" || echo "proxy: MISSING"
```

Expected: `proxy: set`. (Don't echo the value — it contains the proxy password.)

## Step 1 — proxy reachability + single fetch

```bash
uv run python -c "
from apfun.sourcing.reddit import _build_client, _build_headers
with _build_client() as c:
    r = c.get('https://www.reddit.com/r/SaaS/new.json', headers=_build_headers(), timeout=30.0)
print('status:', r.status_code, 'bytes:', len(r.content))
"
```

**Expected (green):** `status: 200` with a non-trivial byte count (tens of KB).

**If `403`:** the proxy IP is blocked or the UA/header set isn't passing. Note it and continue to step 2 to see whether it's consistent.

**If `RuntimeError: APFUN_REDDIT_HTTP_PROXY is required`:** env var didn't load — restart wasn't picked up, or `.env` path is wrong.

**If a connection/proxy error** (`ProxyError`, timeout): the proxy credentials or host:port are wrong, or the provider's endpoint is down.

## Step 2 — single-source ingest

```bash
uv run python -c "
from apfun.db import SessionLocal
from apfun.models import Source
from apfun.sourcing.reddit import ingest

with SessionLocal() as s:
    src = s.query(Source).filter_by(kind='reddit', name='r/SaaS').first()
    if src is None:
        src = Source(kind='reddit', name='r/SaaS', config_json={'subreddits': ['SaaS'], 'fetch_kind': 'new'})
        s.add(src); s.flush()
    result = ingest(s, src)   # builds the proxy client itself
    s.commit()
    print(result)
"
```

**Expected (green):** `IngestResult(items_captured=15-25, status_codes=[200], error_class=None, ...)`.

## Step 3 — small batch across 3 subs

```bash
uv run python -c "
from apfun.db import SessionLocal
from apfun.models import Source
from apfun.sourcing.reddit import ingest_batch

with SessionLocal() as s:
    sources = []
    for sub in ['SaaS', 'Entrepreneur', 'startups']:
        src = s.query(Source).filter_by(kind='reddit', name=f'r/{sub}').first()
        if src is None:
            src = Source(kind='reddit', name=f'r/{sub}', config_json={'subreddits': [sub], 'fetch_kind': 'new'})
            s.add(src); s.flush()
        sources.append(src)
    for r in ingest_batch(s, sources):
        print(r)
    s.commit()
"
```

**Expected (green):** three IngestResult rows, all `status_codes=[200]`, `items_captured > 0`.

If the log shows `reddit.ua_block_detected` (>50% 403s), the proxy pool is being rejected batch-wide — that's the "proxy blocked" outcome below.

## Step 4 — capture artifacts

```bash
sqlite3 data/apfun.db <<EOF
SELECT job_id, ok, items_processed, started_at FROM scheduler_runs
WHERE job_id LIKE 'reddit%' ORDER BY id DESC LIMIT 5;
SELECT COUNT(*) FROM raw_signals
WHERE source_id IN (SELECT id FROM sources WHERE kind='reddit');
EOF
```

Record: HTTP status distribution (200/403/429), total rows inserted, approximate proxy bandwidth used (from the provider dashboard), anything operationally surprising.

## Step 5 — refresh the fixture (only if green)

```bash
uv run python scripts/capture_reddit_fixture.py --subreddit SaaS --kind new \
    --reason "post-task-005c proxy + browser-UA migration"
```

The fixture's `_fixture_meta.source` should read `... (proxy)`.

## Outcomes → next move

- **Proxy works, content flows (green).** Open a brief request 022 with the artifacts; mark task 005c done; Reddit sources go back into rotation. Land the green-framed Lesson Learned if not already.
- **Proxy blocked (red).** Reddit detects the proxy IP pool. Try the fallback provider (IPRoyal). If that also fails, accept Reddit-from-this-server is not viable for now — disable Reddit sources in `scripts/seed_sources.py` and escalate to the orchestrator about task 005d (Playwright/JS-capable client) vs dropping Reddit.
- **Partial (yellow).** Some 200s, some 403/429s. Could be rate-limit tuning (lower the `_BUCKET` rate) or a flaky proxy pool. Surface the pattern in request 022 before tuning blindly.

## Artifacts to bring back to the orchestrator (any non-green outcome)

1. Exact failure: status codes, error class, stderr from the failing step.
2. Whether step 1 (raw proxy fetch) succeeded — isolates "proxy itself blocked" from "ingest logic issue."
3. Proxy provider + plan (free vs paid, rotating vs sticky).
4. `scheduler_runs` rows from step 4.
