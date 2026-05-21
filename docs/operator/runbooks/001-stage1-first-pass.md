# Runbook 001 — Stage 1 first-pass against real data

**Goal:** capture the empirical artifacts orchestrator request 018 needs to route between tasks 011 (Stage 2 demand check), 013+014 (admin UI inbox), and prompt iteration on `cluster.j2`. Per feedback 017 Q4.

**Budget guard:** mental cap of **$5** for the entire run. The dump script flags this automatically. If `est_cost_usd` totals approach $5 mid-run, stop and diagnose before continuing.

**Expected scale:** 2-3 subreddits × ~25 posts = ~50-75 raw signals → ~40-60 `signal_text` rows → ~5-15 `candidates`. Enough to read cluster quality, not enough to burn meaningful budget.

**Caveat — `.venv` regression workaround.** If the host `docker-compose.yml` still has the `.venv` named-volume (per feedback 015 action item), every `uv run` below needs `UV_PROJECT_ENVIRONMENT=/tmp/apfun-venv` prefixed. Test once: `uv run python -c "print(1)"`. If it complains about `Permission denied (os error 13)` on `.venv/CACHEDIR.TAG`, use the prefix; otherwise omit it. Examples below omit it for readability.

---

## Step 0 — env

```bash
# In the container shell (/workspace).
export APFUN_ANTHROPIC_API_KEY='sk-ant-...'    # your real key
export APFUN_REDDIT_USERNAME='your_handle'     # any real Reddit handle for the UA
```

Verify:

```bash
echo "${APFUN_ANTHROPIC_API_KEY:0:10}..."   # should print the first 10 chars
echo "${APFUN_REDDIT_USERNAME}"             # should print your handle
```

## Step 1 — migrate + seed

```bash
make init-db
uv run python scripts/seed_sources.py
```

Expected: `Seeded sources: inserted=20+, skipped=...`. The seeder is idempotent — safe to re-run.

## Step 2 — ingest a small Reddit batch

We bypass the full seed list and target a small subset. Inline because there's no scheduler running yet.

```bash
uv run python -c "
from apfun.db import SessionLocal
from apfun.models import Source
from apfun.sourcing.reddit import ingest_batch
from sqlalchemy import select

with SessionLocal() as s:
    sources = s.execute(
        select(Source).where(
            Source.kind == 'reddit',
            Source.name.in_(['r/SaaS', 'r/Entrepreneur', 'r/smallbusiness']),
        )
    ).scalars().all()
    print(f'ingesting {len(sources)} sources')
    results = ingest_batch(s, sources, job_id='runbook.reddit_ingest')
    for r in results:
        print(f'  source_id={r.source_id} items={r.items_captured} statuses={r.status_codes} err={r.error_class}')
    print('total captured:', sum(r.items_captured for r in results))
"
```

Expected: 3 sources × up to 25 items each. Real number depends on subreddit activity since `since_hours=6` is the default. If a source returns `items=0` and `statuses=[200]`, the subreddit just hasn't been active in 6 hours — fine. If any returns `[403]` or `[429]`, you've been rate-limited — wait 5 minutes and retry, or skip that source.

**Spot check:**

```bash
sqlite3 data/apfun.db "SELECT COUNT(*) FROM raw_signals;"
```

If this is < 10, the batch was too sparse. Either re-run after Reddit accumulates more posts, or extend the source list to include `r/indiehackers`, `r/startups`, `r/webdev`.

## Step 3 — normalize

```bash
uv run python -c "
from apfun.db import SessionLocal
from apfun.pipeline.normalize import normalize_raw_signals

with SessionLocal() as s:
    r = normalize_raw_signals(s)
    print(f'normalize: processed={r.processed} inserted={r.inserted} updated={r.updated} skipped={r.skipped} latency_ms={r.latency_ms}')
"
```

Expected: `inserted ≈ raw_signals count - deleted_count`. Idempotent.

## Step 4 — cluster (this is the LLM step; costs money)

```bash
uv run python -c "
from apfun.db import SessionLocal
from apfun.llm.client import LLMClient
from apfun.pipeline.cluster import cluster_signals

with SessionLocal() as s:
    r = cluster_signals(s, llm_client=LLMClient(), job_id='runbook.cluster')
    print(f'cluster: signals={r.processed_signals} buckets={r.buckets} candidates_inserted={r.candidates_inserted} signals_linked={r.signals_linked} capped={r.capped} latency_ms={r.latency_ms}')
"
```

Expected: 1 Haiku call per `signal_text` row + 1 Opus call per bucket. Latency: 1-3 minutes for ~50 signals and ~5-10 buckets.

**Mid-run budget check** — open a second shell while clustering runs (or after the Haiku phase) and check:

```bash
sqlite3 data/apfun.db "SELECT task, COUNT(*), printf('%.4f', SUM(est_cost_usd)) FROM llm_runs GROUP BY task;"
```

If the cumulative `est_cost_usd` approaches **$3** here (60% of the $5 guard), stop and run Step 5 to inspect — likely cause is more buckets than expected.

## Step 5 — dump artifacts

```bash
uv run python scripts/dump_run_artifacts.py > /tmp/runbook-001-artifacts.txt
cat /tmp/runbook-001-artifacts.txt
```

The script prints three sections:

1. **CANDIDATES** — every candidate row with `problem_statement`, `suspected_user`, `seed_keywords`, and up to 5 contributing signals (truncated to ~300 chars each).
2. **LLM_RUNS AGGREGATES** — per-task counts, token statistics, cache hit ratio, total cost. Flags if total exceeds the $5 budget guard.
3. **SCHEDULER_RUNS** — one line per recorded run with duration + error.

## Step 6 — write orchestrator request 018

Open `docs/orchestrator/018-stage1-empirical-input.md` (Claude Code will write this; you only need to bring back the artifacts).

Bring three things to the request:

### (a) Candidates: pick 10 representatives

From the CANDIDATES section, pick:

- The 2-3 best-looking (clear problem statement, grounded in signals, sensible `suspected_user`).
- The 2-3 worst-looking (vague, hallucinated, weak grounding).
- 4-5 median (everything else).

Paste their full text — `problem_statement` + contributing signals — into request 018 verbatim. Don't summarize; let the orchestrator judge quality directly.

### (b) `llm_runs` aggregates

Paste the LLM_RUNS AGGREGATES table verbatim. Include the cache hit ratio line and the GRAND TOTAL COST line.

### (c) Operational observations

Free-form. Things to flag if you noticed any:

- Parse failures (look for `JSONParseError` in `llm_runs.error` or the run log).
- Retries that fired (any `LLMRun.attempts > 1`).
- Cap hits (`ClusterResult.capped == True` in Step 4 output).
- Cost surprises (per-call cost much higher or lower than expected).
- Anything in the log output that "looks weird."

```bash
# Helpful queries for surfacing odd things:
sqlite3 data/apfun.db "SELECT task, attempts, substr(error, 1, 200) FROM llm_runs WHERE ok=0 OR attempts > 1;"
sqlite3 data/apfun.db "SELECT job_id, ok, items_processed, substr(error, 1, 200) FROM scheduler_runs;"
```

## Routing matrix (per feedback 017)

Request 018 will surface the data; the orchestrator will decide. As pre-committed priors:

| Cluster quality | Next task |
|---|---|
| **70%+ reviewable** — clear problems, grounded signals, valid contributing_ids | 013 + 014 bundled (admin UI + inbox endpoint) |
| **Noticeably noisy** — lots of junk, partial hallucinations, weak problem statements | 011 (Stage 2 demand check) first to filter |
| **Unusable** — mostly nonsense, frequent hallucinations, wrong contributing_signal_ids | Prompt iteration on `apfun/llm/prompts/cluster.j2` |

Don't lock yourself in — actual data might suggest a fourth bucket. But these are the prior heuristics.

---

## If something goes wrong

- **Reddit rate-limit (`statuses=[429]`)** — wait 5 minutes; reduce sources to one subreddit.
- **Anthropic auth error** — `APFUN_ANTHROPIC_API_KEY` not exported or invalid.
- **`Permission denied (os error 13)` on `.venv`** — see the caveat at the top; prefix every command with `UV_PROJECT_ENVIRONMENT=/tmp/apfun-venv`.
- **Cluster produced 0 candidates** — most likely cause: < 5 signals made it through normalize. Re-check Step 2 spot-check; consider extending source list and re-running ingest.
- **`est_cost_usd` is much higher than $5** — `_MAX_BUCKETS_PER_RUN=50` is the soft cap; should never reach $5 on ~50 signals. If it does, surface it as the most important operational observation in request 018; this is the signal that PRICING or thinking-budget assumptions are wrong.

## Re-running

The pipeline is idempotent at each stage:

- Re-running ingest only adds new posts (content_hash dedup).
- Re-running normalize updates existing `signal_text` rows in place.
- Re-running cluster skips signals already linked via `candidate_signals`.

So the runbook is safe to re-execute from any step. To **reset** for a fresh run:

```bash
rm -f data/apfun.db
make init-db
uv run python scripts/seed_sources.py
# ... then re-execute from Step 2
```
