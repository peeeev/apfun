# Request 020: task 005b — Reddit OAuth migration

**Date:** 2026-05-22

**Context.** Per the operator's chat session and feedback 018 Q4: Reddit datacenter-IP blocking has now been persistent across multiple runbook attempts and scheduler-readiness sessions. The 403 responses are an IP/UA pattern, not 429 rate-limiting — Reddit's known datacenter discriminator. The feedback-012 trigger ("when Stage 1 logs Reddit 429s with any frequency, open an orchestrator request — that's the trigger for OAuth migration") is satisfied in spirit; 403s from the same root cause count.

Empirical state: runbook 001 ran Reddit with `APFUN_REDDIT_USERNAME` set to a real handle, UA conformant per the established `# heuristic` format, and all 3 sources still 403'd. The auto-disable mechanism + UA-block batch guard fired correctly (no false positives — the batch fraction triggered, no per-source counter increments), which validates that current behavior is "fail safely," but Reddit signal is currently zero from this server.

The decision from the chat session: try OAuth before paying for residential proxies. OAuth is Reddit's official supported path for programmatic access; the quota becomes 100 QPM (vs ~10 unauth); and OAuth requests are empirically less hostile to datacenter IPs than anonymous ones (though not guaranteed — fallback to proxies remains a future option).

## Goal

Migrate `apfun/sourcing/reddit.py` to authenticate via OAuth2 client-credentials flow against `oauth.reddit.com` instead of unauthenticated requests to `www.reddit.com/.json`. Preserve all existing behavior (IngestResult shape, batch wrapper, UA-block guard, three-strikes auto-disable, content hashing, deletion tagging, schema contract test). The only changes should be: how the HTTP request is made, what credentials are required, and the base URL.

## Scope

**In scope:**

- New Reddit "script" app registration documented in operator-facing notes (the operator does this part once, then sets credentials).
- Two new env vars under the `APFUN_` prefix, both *loud-failure* (per the auth-secret discipline from feedback 013):
  - `APFUN_REDDIT_CLIENT_ID`
  - `APFUN_REDDIT_CLIENT_SECRET`
- Auth token acquisition + refresh logic. Tokens last ~1h; refresh transparently when expired.
- Base URL change: `https://www.reddit.com/r/<sub>/.json` → `https://oauth.reddit.com/r/<sub>/.json`. Same JSON response shape — the schema contract test should pass unchanged (verify).
- UA format unchanged (per `# heuristic` reasoning that PRAW-style UAs are recognized regardless of auth path).
- `APFUN_REDDIT_USERNAME` remains required (fail-loud at `Settings()`) — Reddit's UA still demands the `by /u/<handle>` suffix even on OAuth requests.
- Token request must be POST to `https://www.reddit.com/api/v1/access_token` with Basic auth (`client_id`/`client_secret`) and form body `grant_type=client_credentials`. The response shape includes `access_token`, `token_type`, `expires_in`, `scope`. Verify against Reddit's API docs.

**Out of scope:**

- Other ingesters. Don't change HN/PH/IH/review-sites behavior. The auth-secret convention from feedback 013 covers all three modes; this PR exercises the loud-failure mode for the first time at an actual call site.
- Proxy fallback. If OAuth from this server still gets blocked (operator can verify in a runbook session), that's a future PR with `APFUN_REDDIT_PROXY` env var.
- Refresh tokens / user-context auth. Client-credentials flow is sufficient for read-only ingestion.

## Status-code distinction (important)

Reddit's 403 response from this server is *not* the UA-block-guard-triggering case from feedback 009. That guard was designed for "Reddit blocks our anonymous request based on UA/IP signature" — which the operator confirmed empirically is what's happening.

After OAuth migration:

- **401 Unauthorized** from `oauth.reddit.com` → token is invalid or expired. Refresh and retry. This is the new transient case that didn't exist before.
- **403 Forbidden** from `oauth.reddit.com` → genuinely terminal (subreddit private, app-banned, etc.). Same as the existing `TERMINAL_STATUSES` handling.
- **429 Too Many Requests** → standard rate-limit. Backoff + retry per existing pattern.
- **5xx** → transient per existing pattern.

The UA-block batch guard (`_UA_BLOCK_BATCH_FRACTION = 0.5`) becomes effectively dead code post-migration — OAuth requests don't produce that pattern. **Don't remove it.** Leave as a defensive check in case Reddit changes policy and the pattern returns. Annotate inline that it's defensive post-OAuth.

## Implementation shape

Suggested approach; deviate if you see a better path:

```python
# apfun/sourcing/reddit.py
@dataclass
class _OAuthToken:
    access_token: str
    expires_at: datetime

class _RedditAuth:
    """Owns the OAuth token lifecycle. Thread-safe lazy refresh."""
    def __init__(self, client_id: str, client_secret: str, user_agent: str):
        self._client_id = client_id
        self._client_secret = client_secret
        self._user_agent = user_agent
        self._token: _OAuthToken | None = None
        self._lock = threading.Lock()

    def get_token(self, client: httpx.Client) -> str:
        with self._lock:
            if self._token is None or self._token.expires_at < utcnow() + REFRESH_SKEW:
                self._token = self._fetch_token(client)
            return self._token.access_token

    def _fetch_token(self, client: httpx.Client) -> _OAuthToken:
        # POST to https://www.reddit.com/api/v1/access_token with Basic auth
        ...
```

The auth object is shared across batch-mate sources (single token per batch). Per-source `ingest()` calls `get_token()` before each fetch; the lock ensures concurrent batch members don't double-refresh.

Two new constants:

```python
# verified 2026-05-22 https://www.reddit.com/dev/api — OAuth token endpoint.
_REDDIT_OAUTH_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_REDDIT_OAUTH_API_BASE = "https://oauth.reddit.com"

# heuristic 2026-05-22 — refresh tokens 60s before expiry to avoid mid-fetch
# 401s. Reddit tokens have ~1h lifetime; this skew is well under that.
REFRESH_SKEW = timedelta(seconds=60)
```

## Tests

- **Unit tests** for token acquisition, refresh-on-expiry, retry-on-401-with-refresh, concurrent refresh under lock (don't double-fetch), missing-credentials fail-loud at first call.
- **Schema contract test** unchanged — captured fixtures should still validate (Reddit's JSON shape is identical between `www.` and `oauth.`).
- **Integration test** gated on real credentials. Fetches one subreddit, asserts ≥1 row inserted, captures a fresh fixture via the existing `scripts/capture_reddit_fixture.py` (which needs updating to use OAuth path).
- The UA-block batch guard test stays — verify the defensive code path is reachable in a test mock even if production-rare.

## Constants verification

Per the `# verified` / `# heuristic` convention:

- `_REDDIT_OAUTH_TOKEN_URL` → `# verified` against current Reddit API docs.
- `_REDDIT_OAUTH_API_BASE` → `# verified` against current Reddit API docs.
- OAuth response field names (`access_token`, `token_type`, `expires_in`, `scope`) → `# verified` against the OAuth response shape in Reddit's docs.
- `REFRESH_SKEW = 60s` → `# heuristic` with rationale.
- `_REDDIT_UNAUTH_QPM_CEILING` becomes irrelevant post-migration. Replace with `_REDDIT_OAUTH_QPM_CEILING = 100` annotated `# verified` against Reddit's published OAuth quota.

Run `grep -r '# TODO verify' apfun/sourcing/reddit.py` and escalate any unresolved at task end (per feedback 012's pattern).

## Operator setup (document in PR description)

The operator does this once:

1. Visit https://www.reddit.com/prefs/apps
2. "Are you a developer? Create an app..." → name "apfun-funnel", select "script" type
3. Description optional; redirect URI `http://localhost:8080` (unused for client-credentials flow but required by the form)
4. After creation: client_id is shown under the app name; client_secret is the "secret" string
5. Set on the host: `APFUN_REDDIT_CLIENT_ID=...` and `APFUN_REDDIT_CLIENT_SECRET=...` (likely in the docker-compose env file or a `.env` next to it)
6. Restart the container for env vars to load

## Documentation updates (in the SAME PR, not a follow-up)

All of these update together with the code:

1. **`CLAUDE.md` → Networking section.** Document `APFUN_REDDIT_CLIENT_ID` + `APFUN_REDDIT_CLIENT_SECRET`. Update the Reddit-specific note from the anon-path framing to the OAuth-path framing. `APFUN_REDDIT_USERNAME` documentation stays — still required for UA.

2. **`.env.example`** (operator-facing template). Add placeholder lines for the two new credentials with a comment pointing at `https://www.reddit.com/prefs/apps`.

3. **`README.md` or `docs/operator/SETUP.md`** (whichever has setup instructions). One new setup step: "Register a Reddit OAuth script app, copy credentials." Lift the 6-step operator-setup procedure from above into operator-facing docs.

4. **`docs/operator/runbooks/002-reddit-oauth-first-pass.md`** — new runbook for the post-PR empirical test ("does OAuth from this server's IP actually work?"). Small: set env vars, run one ingest against r/SaaS, observe 200 vs 401/403, capture cost + outcome. This is the runbook-001-shaped empirical session that confirms whether OAuth solves the datacenter blocking or whether a proxy follow-up is needed.

5. **`docs/tasks/000-overview.md`**. Note task 005b in the cross-cutting/follow-ups section: "005b: Reddit OAuth migration (post-task-005 follow-up after datacenter-IP blocking persisted)."

6. **`docs/tasks/005-reddit-ingester.md`** — footer note pointing at 005b: "Updated to OAuth in task 005b after datacenter-IP blocking persisted. See `005b-reddit-oauth.md`."

7. **`docs/orchestrator/INDEX.md`** — row 020 → answered after this PR merges (standard pattern).

8. **`CLAUDE.md` → Lessons Learned, dated 2026-05-22-or-when-test-confirms**, ONLY after runbook 002 confirms OAuth works (or doesn't). Suggested wording:

   > **Datacenter IPs face source-specific hostility patterns.** Reddit's anonymous endpoint is functionally unusable from cloud providers (Hetzner, AWS, OVH); OAuth migration is the official-supported workaround. When a source's API behaves differently from a datacenter IP than from a residential one, official auth paths should be preferred over header/UA tweaks before reaching for proxies. If OAuth alone is insufficient, residential proxies are the next step — but OAuth first, proxies second.

   If runbook 002 shows OAuth was insufficient on its own and a proxy was still needed, adjust the wording accordingly.

9. **`CLAUDE.md` → Conventions** — add the docs-update discipline as a sibling rule to the verify-constants and contract-test conventions:

   > **Docs update with the code.** When a task changes external interfaces, env vars, conventions, or operator procedures, the relevant docs (CLAUDE.md, README, `.env.example`, operator runbooks, task specs) are updated in the same PR — not as a follow-up. Documentation drift is the silent killer of long-running projects; the cost of writing docs when the change is fresh is much lower than reconstructing context weeks later.

## What I would do next without intervention

1. Branch `feature/task-005b-reddit-oauth`.
2. Add the two settings fields with loud-failure validators per feedback 013's auth-secret discipline (`Settings()` allows missing values; first OAuth call site raises clear error pointing at CLAUDE.md → Networking).
3. Implement `_RedditAuth` per the sketch above.
4. Migrate `_fetch_subreddit_listing` (or whatever it's currently called) to call `get_token()` and use the `oauth.reddit.com` base URL.
5. Add tests per the Tests section.
6. Update `scripts/capture_reddit_fixture.py` to use OAuth (so future captures don't fall back to the broken anonymous path).
7. Apply documentation updates 1-7 from the section above (CLAUDE.md Networking, `.env.example`, operator SETUP, new runbook 002, overview, task 005 footer, INDEX.md update).
8. Add the "Docs update with the code" convention to CLAUDE.md → Conventions (item 9 above) — this convention is a small generalization worth landing while it's fresh.
9. Run the `grep -r '# TODO verify' apfun/sourcing/reddit.py` discipline; ensure result is zero.
10. Open PR with the operator setup procedure in the description.
11. **Defer** items 8 (Lesson Learned) until runbook 002 confirms whether OAuth actually fixed the datacenter blocking. The Lesson Learned wording depends on the empirical result; don't pre-commit to either framing.

## Specific questions or risks

1. **Does OAuth actually solve the datacenter-IP blocking?** Empirical question that only the post-PR test answers. If OAuth from this server still 403s, the next move is `APFUN_REDDIT_PROXY` — but that's a separate PR. **For now: ship OAuth, run a small empirical test (the operator can do a one-off `runbook-shaped` test session after the PR merges), and decide proxy-or-not based on the result.**

2. **Token lifecycle thread safety.** The batch wrapper currently calls per-source `ingest()` sequentially within one batch. If that ever becomes concurrent (no plan for it, but possible if HN ingestion grows enough to warrant parallel sources), the `threading.Lock` in `_RedditAuth` protects against concurrent refresh. Note in comment that the lock is forward-defensive.

3. **Captured Reddit fixtures from the anonymous path** — do they need re-capture? The JSON shape should be identical between `www.reddit.com/.json` and `oauth.reddit.com/.../listing`, so the schema contract test should pass unchanged. Verify by running the contract test against existing fixtures after migration; if it fails, recapture. Don't preemptively recapture.

4. **Removing the unauth fallback.** Should the ingester retain *any* unauthenticated fallback for development environments without credentials? **Recommend: no.** Loud-failure auth pattern says "missing credentials → fail at first call site." The mixed-mode fallback adds complexity without value; dev environments either set the credentials or don't run Reddit ingest.

## Relevant files

Code under change:
- `apfun/sourcing/reddit.py` — primary file
- `apfun/config.py` — add the two new env-var fields
- `apfun/sourcing/_base.py` — no change expected (auth lives in the source module, not the base)
- `tests/unit/test_reddit_ingester.py` — extend with OAuth-specific tests
- `tests/integration/test_reddit_live.py` — update to use real credentials
- `scripts/capture_reddit_fixture.py` — update to use OAuth path

Docs under change (same PR):
- `CLAUDE.md` — Networking section update + new docs-update convention
- `.env.example` — two new placeholder lines
- `README.md` or `docs/operator/SETUP.md` — Reddit OAuth registration step
- `docs/operator/runbooks/002-reddit-oauth-first-pass.md` — new runbook for post-PR empirical test
- `docs/tasks/000-overview.md` — task 005b cross-cutting entry
- `docs/tasks/005-reddit-ingester.md` — footer pointing at 005b
- `docs/orchestrator/INDEX.md` — row 020 → answered after PR merge

Deferred (after runbook 002):
- `CLAUDE.md` — Lessons Learned entry, wording depends on empirical result
