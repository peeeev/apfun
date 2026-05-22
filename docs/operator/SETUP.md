# Operator setup

One-time configuration for the apfun container before first run. Each section maps to one external service the ingester or LLM client talks to.

The container is built and started via the host's `docker-compose.yml` at `/srv/claude/apfun.online/` — this guide assumes that exists. Everything below configures *env vars* that the container reads; the canonical place for them is the `.env` file next to the host docker-compose. See `.env.example` in the repo for the full template.

After any change to env vars, restart the container so the new values load:

```bash
docker compose -f /srv/claude/apfun.online/docker-compose.yml restart
```

## Reddit OAuth (task 005b)

`apfun/sourcing/reddit.py` authenticates via OAuth2 client-credentials. Datacenter IPs were 403'd persistently on Reddit's anonymous endpoint; OAuth is the supported workaround. One-time setup:

1. Visit https://www.reddit.com/prefs/apps while logged in.
2. Click **"Are you a developer? Create an app..."** at the bottom.
3. Fill in:
   - **name**: `apfun-funnel` (or anything; doesn't matter)
   - **type**: select **`script`** (NOT "web app")
   - **description**: optional
   - **about url**: leave blank
   - **redirect uri**: `http://localhost:8080` (unused by client-credentials flow but the form requires *some* value)
4. Click "create app". The result page shows:
   - **client_id**: the short string under the app name (looks like `aB3xK7gR9pX2`).
   - **client_secret**: the longer string labeled "secret" (looks like `XyZ1234...`).
5. Set on the host (e.g. in `/srv/claude/apfun.online/.env`):
   ```
   APFUN_REDDIT_CLIENT_ID=<client_id from step 4>
   APFUN_REDDIT_CLIENT_SECRET=<client_secret from step 4>
   APFUN_REDDIT_USERNAME=<your Reddit handle, no leading u/>
   ```
6. Restart the container.

Verification: run `docker exec -it apfun-funnel uv run python -c "from apfun.sourcing.reddit import _get_auth; import httpx; print(_get_auth().get_token(httpx.Client()))"` — should print a token string. If you get the `Reddit OAuth credentials are missing` error, env vars didn't load (restart wasn't picked up, or `.env` path wrong).

Whether OAuth actually fixes the datacenter-IP blocking is empirical — runbook 002 (`docs/operator/runbooks/002-reddit-oauth-first-pass.md`) is the post-merge test that confirms it. If OAuth alone is insufficient, the next step is residential proxies (separate future PR), not more header tweaks.

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
