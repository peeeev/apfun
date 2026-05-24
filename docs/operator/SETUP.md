# Operator setup

One-time configuration for the apfun container before first run. Each section maps to one external service the ingester or LLM client talks to.

## Web URLs (behind Apache htpasswd)

Everything under `apfun.online` is protected by the vhost's basic-auth; the app itself does no auth.

- **`/inbox`** — HITL review queue: pending candidates, approve/reject.
- **`/ops`** — read-only operator dashboard (task 024): KPI cards, scheduler job calendar with STALE warnings, recent runs, source health, LLM cost, recent errors. Auto-refreshes every 30s. Desktop-oriented. This is the at-a-glance health view — check it instead of SSH-ing in to run `sqlite3` queries.
- **`/inbox/<id>`** — candidate detail view (task 014-fix-1): every contributing signal with its text, source label, and a link to the original post; plus decision history.
- **`/inbox/approved`**, **`/inbox/rejected`**, **`/inbox/unsure`** — status-filtered listings (task 014-fix-1). Any listed candidate can be re-decided (approve/reject/unsure with optional notes). "Unsure" = looked but couldn't decide (distinct from pending = not yet looked at).
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
