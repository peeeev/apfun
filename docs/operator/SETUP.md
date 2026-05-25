# Operator setup

One-time configuration for the apfun container before first run. Each section maps to one external service the ingester or LLM client talks to.

## Web URLs (behind Apache htpasswd)

Everything under `apfun.online` is protected by the vhost's basic-auth; the app itself does no auth.

- **`/inbox`** — HITL review queue: pending candidates, approve/reject. Nav links show live counts (`pending (N)`, `approved (N)`, …). Tick the per-card
  checkboxes and hit **"merge N selected"** (2+ required) to combine duplicates into one Opus-synthesized candidate (task 014-fix-2).
- **`/ops`** — operator dashboard (task 024 + 023-fix-1 + 014-fix-2): KPI cards, scheduler job calendar with STALE warnings, recent runs, source health, LLM
   cost, recent errors. Auto-refreshes every 30s. Desktop-oriented. This is the at-a-glance health view — check it instead of SSH-ing in to run `sqlite3` queries.
   The Scheduler section has a status pill (running/paused/stopped) and three buttons: **stop**/**resume** (global pause/resume — only scheduled jobs stop;
   triage + manual runs keep working; pause survives container restarts) and **restart** (tears down + restarts APScheduler in place when a job is STALE,
   without restarting uvicorn). Each logs to `scheduler_runs` (`ops.manual_pause` / `ops.manual_resume` / `ops.manual_restart`).
- **`/inbox/<id>`** — candidate detail view (task 014-fix-1): every contributing signal with its text, source label, and a link to the original post; plus
  decision history. A merged-away candidate redirects here to the candidate it was merged into.
- **`/inbox/approved`**, **`/inbox/rejected`**, **`/inbox/unsure`** — status-filtered listings (task 014-fix-1). Any listed candidate can be re-decided
  (approve/reject/unsure with optional notes). "Unsure" = looked but couldn't decide (distinct from pending = not yet looked at).

- `/opportunities`, `/sources`, `/projects` — placeholders until tasks 020/021.

The container is built and started via the host's `docker-compose.yml` at `/srv/claude/apfun.online/` — this guide assumes that exists. Everything below configures *env vars* that the container reads; the canonical place for them is the `.env` file next to the host docker-compose. See `.env.example` in the repo for the full template.

After any change to env vars, restart the container so the new values load:

```bash
docker compose -f /srv/claude/apfun.online/docker-compose.yml restart
```

## Reddit access (task 005c)

`apfun/sourcing/reddit.py` reaches Reddit's public JSON endpoints through a **residential proxy** with **browser-mimicking UAs**. Two independent blocks are in play (see CLAUDE.md → Networking): datacenter IPs are network-blocked, and the web frontend filters non-browser UAs. The browser-UA half is handled internally — the only operator config is the proxy.

> Background: this replaces the abandoned OAuth approach (task 005b). Reddit closed self-service OAuth credential creation in November 2025 (Responsible Builder Policy), so there's no app to register anymore.

One-time setup:

1. Pick a residential-proxy provider. Options as of 2026-05:
   - **Webshare** — free tier (10 proxies); $3.50/mo rotating residential. Reddit-tested. Recommended starting point.
   - **IPRoyal** — $1.75/GB pay-as-you-go, non-expiring. Good for variable volume.
   - Decodo / Oxylabs — enterprise-priced ($75+/mo); overkill for v1.
2. From the provider dashboard, grab the proxy URL in `http://username:password@host:port` form. Providers that assign one IP per port (e.g. Webshare `p.webshare.io:8000`, `:8001`) — pick one port; the env var takes a single URL.
3. Set on the host (e.g. in `/srv/claude/apfun.online/.env`):
   ```
   APFUN_REDDIT_HTTP_PROXY=http://username:password@host:port
   ```
4. Restart the container.

Verification: run `docker exec -it apfun-funnel uv run python -c "from apfun.sourcing.reddit import _build_client; c=_build_client(); print(c.get('https://www.reddit.com/r/SaaS/new.json', headers=__import__('apfun.sourcing.reddit', fromlist=['_build_headers'])._build_headers()).status_code)"` — should print `200`. A `403` means the proxy IP is itself blocked (try another provider/port); the `APFUN_REDDIT_HTTP_PROXY is required` error means the env var didn't load.

Whether the proxy + browser-UA actually gets through is empirical — runbook 003 (`docs/operator/runbooks/003-reddit-proxy-first-pass.md`) is the post-merge test that confirms it. If it's blocked despite the proxy, the next escalation is a JS-capable client (Playwright), task 005d.

## Anthropic API

`apfun/llm/client.py` hits the Anthropic API for every judgment call (Stage 1 clustering, Stage 4 scoring, Stage 5 synthesis, etc.). One-time setup:

1. Get a key from https://console.anthropic.com/.
2. Set `APFUN_ANTHROPIC_API_KEY=<your key>` in the host `.env`.
3. Restart.

Loud-failure: missing/empty key surfaces at first LLM call with a clear error. The wrapper doesn't pre-validate at `Settings()` construction because the Anthropic API returns a structured 401 on bad keys, not silent garbage.

## ProductHunt token (optional)

Only needed if you enable ProductHunt ingestion (task 007). Same loud-failure shape as Anthropic — set `APFUN_PRODUCTHUNT_TOKEN` and restart.

## Post-rebuild bootstrap

After any container rebuild (`docker compose up -d --build`), some state lives outside the image and needs to be re-established. Run:

```bash
docker exec -it apfun-funnel /workspace/scripts/post-rebuild-bootstrap.sh
```

This installs the `sqlite3` CLI (handy for poking the DB) and verifies `gh auth status`. Idempotent.

## Deploying code + running migrations

Code and the live DB both live in the bind-mounted `/workspace` (which stays checked out on `main`). uvicorn runs with `--reload`, so:

**Deploy code (after a PR merges):**

```bash
cd /workspace        # (or the host bind path) — must be on main
git pull             # uvicorn --reload picks up the new code automatically; no restart
```

**Run a migration — ALWAYS snapshot first** (per the post-incident backup discipline; a batch migration with no backup is how `candidate_signals` + `approvals` were lost once):

```bash
make migrate         # snapshots data/apfun.db → data/backups/, THEN alembic upgrade head
```

Prefer `make migrate` over a bare `alembic upgrade head` — it runs `scripts/db_snapshot.sh` first (consistent online backup, keeps the most recent 10 in `data/backups/`, gitignored). To snapshot without migrating: `make snapshot`. If a migration ever goes wrong, restore by copying the relevant `data/backups/apfun-<rev>-<ts>.db` back to `data/apfun.db` (with the app stopped).

## Buildability backfill (task 025)

The cluster pass assesses **buildability** (`high`/`medium`/`low`/`non_software`) for every *new* candidate inline. Candidates created before the buildability layer existed have `buildability IS NULL` and need a one-time backfill so the inbox badges are consistent. After the migration that adds the columns (`4e8f1a2b9c3d`) is applied (snapshot the DB first — see the deploy/migration ritual above):

```bash
# 1. Eyeball a handful first (no commitment) — spot-check the values look sane.
APFUN_ANTHROPIC_API_KEY=... uv run python scripts/backfill_buildability.py --limit 10

# 2. Full backfill of all unassessed candidates.
APFUN_ANTHROPIC_API_KEY=... uv run python scripts/backfill_buildability.py
```

It's idempotent (skips already-assessed candidates; per-candidate commit, so a crash is safe to resume by re-running) and aborts if the run cost exceeds `--budget` (default $5; expect ~$1.25 for ~168 candidates). `--dry-run` lists what would be assessed without spending a token. On a **fresh install** with no pre-existing candidates, there's nothing to backfill — new candidates get buildability at cluster time.

After the backfill, refresh `/inbox` and verify the badges render; the detail view (`/inbox/<id>`) shows each candidate's buildability rationale. Buildability is a *hint* — you can still approve a `non_software` candidate; the `☐ hide non-software` toggle filters them from the listing if you want.

## Scheduler pause + candidate merge (task 014-fix-2)

**Pause the stream during a triage session.** On `/ops`, the Scheduler section's **stop** button pauses all scheduled ingest + pipeline jobs (the status pill turns yellow/"paused"). Triage, manual cluster runs, and LLM calls keep working — only the background firing stops. **resume** restarts firing. The pause is persisted, so it survives a container restart until you explicitly resume (it does NOT silently un-pause on deploy). Use **restart** (not stop/resume) for a STALE/wedged job.

**Merge duplicates.** In any `/inbox` listing, tick the checkbox on 2+ candidates that are the same underlying problem and click **"merge N selected"**. Opus synthesizes a unified problem statement (+ re-assessed buildability); the sources are soft-deleted (hidden from listings; their detail pages redirect to the merged one) and their signals re-linked to the new candidate. The merged candidate is **pending** — re-triage it. Merging is not reversible in v1 (the `merged_into_id` chain is the audit trail). ~$0.013 per merge.
