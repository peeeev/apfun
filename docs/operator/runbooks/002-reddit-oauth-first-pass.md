# Runbook 002 — Reddit OAuth first-pass

> **ABANDONED — do not run.** Reddit closed self-service OAuth credential creation in November 2025 (Responsible Builder Policy), so there's no app to register and no token to fetch. The OAuth code this runbook tested was reverted in task 005c. The current Reddit access path (residential proxy + browser-mimicking UA) is validated by **runbook 003** instead. Kept for the paper trail. Per orchestrator request 021.

**Goal:** confirm whether OAuth solves the datacenter-IP blocking that runbook 001 surfaced on Reddit's anonymous endpoint. Empirical question per orchestrator request 020 §Specific questions or risks Q1.

**Expected outcome:** one of:
- **Green** — OAuth fetch returns 200 with real listing data. Migration complete; proceed normally.
- **Yellow** — OAuth fetch returns 200 sometimes, 403 sometimes. Reddit is fingerprinting deeper than UA — escalate to orchestrator for residential-proxy follow-up PR (`APFUN_REDDIT_PROXY`).
- **Red** — OAuth fetch consistently 401s after refresh, or consistently 403s. Indicates either bad credentials (401s) or Reddit datacenter-IP block survives auth (403s). Escalate to orchestrator with the captured artifacts.

**Budget guard:** none — Reddit's API is free under OAuth client-credentials. The only cost is operator time (~10 minutes).

**Prerequisites:** task 005b merged + container restarted + the three env vars set per `docs/operator/SETUP.md` → Reddit OAuth.

---

## Step 0 — env sanity

```bash
docker exec -it apfun-funnel bash
cd /workspace

echo "${APFUN_REDDIT_USERNAME}"
echo "${APFUN_REDDIT_CLIENT_ID}"
# (don't echo the secret — just check it's non-empty)
[ -n "${APFUN_REDDIT_CLIENT_SECRET}" ] && echo "secret: set" || echo "secret: MISSING"
```

Expected: all three non-empty, the secret marked `set`.

## Step 1 — verify token acquisition

```bash
uv run python -c "
from apfun.sourcing.reddit import _get_auth
import httpx
with httpx.Client() as c:
    token = _get_auth().get_token(c)
print('token:', token[:12] + '...', 'len=', len(token))
"
```

**Expected:** prints a token string (typically ~30+ chars, base64-ish).

**If it raises** `Reddit OAuth credentials are missing`: env vars didn't load. Restart didn't pick up the `.env`, or the `.env` is at the wrong path. Capture the error and the contents of `/srv/claude/apfun.online/.env` (with the secret redacted) for the orchestrator.

**If it raises an HTTP 401** from `client.post`: credentials are present but wrong. Re-check that you copied the client_id and client_secret from the right fields on the Reddit app page.

## Step 2 — single-source listing fetch

```bash
uv run python -c "
from apfun.db import SessionLocal
from apfun.models import Source
from apfun.sourcing.reddit import ingest
import httpx

with SessionLocal() as s:
    src = s.query(Source).filter_by(kind='reddit', name='r/SaaS').first()
    if src is None:
        src = Source(kind='reddit', name='r/SaaS', config_json={'subreddits': ['SaaS'], 'fetch_kind': 'new'})
        s.add(src); s.flush()
    with httpx.Client() as c:
        result = ingest(s, src, client=c)
    s.commit()
    print(result)
"
```

**Expected (green):** `IngestResult(items_captured=15-25, status_codes=[200], error_class=None, latency_ms=300-1500)`.

**Yellow:** `status_codes` contains a mix of 200/401/429/403. `items_captured > 0` is still acceptable but worth flagging.

**Red:** `status_codes=[401, 401]` (refresh-and-retry both failed → bad creds) or `status_codes=[403]` (Reddit is rejecting authenticated requests too — datacenter-IP block beyond auth scope).

## Step 3 — small-batch fetch across 3 subs

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
    results = ingest_batch(s, sources)
    s.commit()
    for r in results:
        print(r)
"
```

**Expected (green):** three IngestResult rows, all with `status_codes=[200]` and `items_captured > 0`.

If the UA-block batch guard fires (>50% 403s), the log will show `reddit.ua_block_detected` — that's the regressed-to-anonymous-path case and means OAuth isn't actually being applied. Investigate before continuing.

## Step 4 — verify schema contract still holds

```bash
uv run pytest tests/unit/test_reddit_schema_contract.py -v
```

Reddit's JSON shape under OAuth *should* be identical to the anonymous path; if this test fails after a real OAuth fetch refreshed the fixture (which step 2/3 above does not do), there's a real shape change and the parser needs updating. With the captured fixture unchanged, this test should remain green.

## Step 5 — sqlite peek at what landed

```bash
sqlite3 data/apfun.db <<EOF
SELECT COUNT(*), MIN(captured_at), MAX(captured_at) FROM raw_signals
WHERE source_id IN (SELECT id FROM sources WHERE kind='reddit');
SELECT s.name, COUNT(rs.id) FROM sources s
LEFT JOIN raw_signals rs ON rs.source_id = s.id
WHERE s.kind = 'reddit' GROUP BY s.id;
EOF
```

Expected: non-zero row count across the three sources, captured_at timestamps within the last few minutes.

## Step 6 — capture a fresh fixture (only if green)

```bash
uv run python scripts/capture_reddit_fixture.py --subreddit SaaS --kind new \
    --reason "post-task-005b OAuth migration"
```

The fixture's `_fixture_meta.source` should now read `GET https://oauth.reddit.com/... (oauth)` instead of the previous anonymous URL.

## Artifacts to bring back to the orchestrator

If the outcome is anything other than clean green:

1. The exact failure: status code, error class, stderr from the step that failed.
2. Output of `sqlite3 data/apfun.db "SELECT * FROM scheduler_runs WHERE job_id LIKE 'reddit%' ORDER BY id DESC LIMIT 5;"`.
3. Whether step 1 (token acquisition) succeeded — narrows "bad creds" vs "Reddit blocking authenticated calls from this IP" decisively.
4. The host's IP geolocation (provider, country, datacenter-vs-residential) — Reddit's discrimination is per-IP-class, so what kind of IP we're on shapes the next decision.

If clean green: nothing to escalate. The migration worked; the scheduler will start picking up Reddit signal on its next firing.

## Lessons Learned (write after running)

Whatever the outcome, add a one-line lesson to `CLAUDE.md` → Lessons learned with today's date. Two candidate framings (pick whichever matches):

- **Green:** "OAuth solved Reddit datacenter-IP blocking on this server. Anonymous path is functionally unusable from cloud providers; official auth is the workaround."
- **Yellow/Red:** "OAuth alone was insufficient for Reddit datacenter-IP blocking; residential proxies needed. Cloud-provider IP fingerprinting reaches deeper than UA."

The runbook outcome decides which framing lands. Per orchestrator request 020 §Documentation item 8.
