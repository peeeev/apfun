# Request 021: task 005c — Reddit residential-proxy access

**Date:** 2026-05-22

**Context.** Task 005b (Reddit OAuth migration) was based on outdated information about Reddit's API access. In November 2025, Reddit introduced the Responsible Builder Policy and effectively ended self-service OAuth credential creation. The operator attempted to register both `web app` and `script` types via the standard developer-apps form; both submissions blocked with a generic "Responsible Builder Policy" message. The submit-request fallback (`support.reddithelp.com/hc/requests/new?ticket_form_id=14868593862164`) is gated to moderation use cases — apfun doesn't qualify.

OAuth path is closed for this project's use case. Strategic pivot: residential proxy + the anonymous public-JSON path that previously worked (pre-OAuth-migration code).

**Additional discovery (added 2026-05-22 mid-spec):** As of June 2025, Reddit's web frontend (`www.reddit.com`) also filters by User-Agent — blocking PRAW-style self-identifying UAs the same way it blocks unrecognized browser UAs. The PRAW-style `apfun-funnel:v0.1 (by /u/<handle>)` convention from feedback 012 was correct for *authenticated* OAuth requests against `oauth.reddit.com`, but the anonymous public-JSON path lives on the web frontend and is treated as browser traffic. The pivot back to the JSON path therefore requires *both* residential proxy IPs *and* browser-mimicking UA + headers. This is two independent blockers that previously presented as one symptom (403s); the residential proxy alone wouldn't have been enough.

## Goal

1. Revert the OAuth code paths from task 005b. The anonymous-JSON ingester code lives in git history pre-005b — restore it as a starting point, but the UA strategy needs further change (see step 3).
2. Add residential-proxy support to `apfun/sourcing/reddit.py` only. Other ingesters (HN, PH, IH, review-sites) remain unchanged.
3. Replace PRAW-style UA with a small pool of browser-mimicking Chrome UAs + full browser header set. Drop the `APFUN_REDDIT_USERNAME` env var since its only purpose was feeding the PRAW format.
4. Validate empirically that Reddit-via-residential-proxy-with-browser-UA from this datacenter IP actually works. Runbook-shaped session after the PR.

## Scope

**In scope:**

- Revert `apfun/sourcing/reddit.py`, `apfun/config.py`, and related test files from the OAuth state introduced in 005b. If task 005b is on `main`: git revert that commit cleanly. If task 005b is still on a feature branch: abandon that branch. Use pre-005b code as a *starting point*, not the final state — UA strategy and credentials change beyond what 005b reverted to.
- Add new env var `APFUN_REDDIT_HTTP_PROXY` to `apfun/config.py`. Standard URL format (`http://username:password@host:port`) accepted directly by `httpx`. **Loud-failure semantics** per the auth-secret discipline (feedback 013): empty default at `Settings()`; first Reddit call site raises `RuntimeError` if the env var is empty and Reddit sources are active.
- Pass the proxy through `httpx.Client(proxy=...)` in `reddit.py` only. No global proxy configuration.
- Remove the OAuth-specific credentials (`APFUN_REDDIT_CLIENT_ID`, `APFUN_REDDIT_CLIENT_SECRET`) from the schema.
- **Also remove `APFUN_REDDIT_USERNAME`.** Its only use was constructing the PRAW-style UA. With the UA change below, it's dead code. Delete the field, the validator, the `.env.example` line, and the test fixture's setting.
- Replace `USER_AGENT` (single PRAW-style string) with `USER_AGENT_POOL` (list of 3-5 recent Chrome UAs). Random pick per request. Annotation describes the new posture honestly.
- Add full browser header set (`Accept`, `Accept-Language`, `Accept-Encoding`, `DNT`, `Sec-Fetch-*`, `Upgrade-Insecure-Requests`) matching what a real Chrome session sends. Set on every request to the Reddit JSON endpoint.
- Update existing UA tests to cover the new rotation behavior. Add a test that asserts browser headers are present on outbound requests.

**Out of scope:**

- Proxying other ingesters. HN/PH/IH/review-sites work from datacenter IPs without proxies; adding proxy overhead is YAGNI.
- Multi-provider proxy abstraction. The env var accepts a single proxy URL; if we ever need provider rotation, that's a future PR.
- Sticky-session vs rotating IP control. Webshare's default rotating residential is fine for read-only public-JSON ingest.

## Provider recommendation (operator's choice)

The implementation accepts any HTTP proxy URL; provider selection is the operator's call. Current landscape (per search 2026-05-22):

- **Webshare** — free tier with 10 proxies; $3.50/month rotating residential. Known for Reddit-specifically-tested integration. Recommended starting point.
- **IPRoyal** — $1.75/GB pay-as-you-go non-expiring. Works for variable-volume use; possibly cheaper if you scale down.
- **Decodo** (formerly Smartproxy) and **Oxylabs** — enterprise-priced ($75+/month). Overkill for v1 volume.

The runbook (below) tests against whatever provider the operator chose; if Webshare's free-tier IPs are pre-blocked by Reddit, fall back to their paid tier or try IPRoyal.

## Implementation shape

In `apfun/config.py`:

```python
class Settings(BaseSettings):
    # ... existing fields ...
    reddit_http_proxy: str = ""  # loud-failure at call site, not here

    @field_validator("reddit_http_proxy", mode="after")
    @classmethod
    def _validate_reddit_proxy(cls, v: str) -> str:
        # Allow empty (no validation) — call site decides if absence is fatal.
        # If non-empty, must look like a URL.
        if v and not v.startswith(("http://", "https://")):
            raise ValueError(
                "APFUN_REDDIT_HTTP_PROXY must be a URL starting with http:// or https://. "
                "Format: http://username:password@host:port. See CLAUDE.md → Networking."
            )
        return v
```

Drop `reddit_username` field, its validator, the `.env.example` line, and any test fixtures setting it. Search the codebase for `APFUN_REDDIT_USERNAME` and `reddit_username` references; they should all go.

In `apfun/sourcing/reddit.py` (rough shape):

```python
import random

# heuristic 2026-05-22 — Reddit's web frontend filters non-browser UAs since
# June 2025. Anonymous JSON-endpoint access requires browser-mimicking
# headers, not PRAW-style self-identification. Pool rotates per request to
# blend with normal traffic patterns. Update UAs every 6-12 months to track
# current Chrome stable releases (older UAs look suspicious).
USER_AGENT_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
]

# heuristic 2026-05-22 — full header set matching what stable Chrome sends to
# reddit.com. UA-only spoofing is detectable; consistent header constellation
# matters. Values are observed-from-Chrome, not published — refresh if Reddit
# starts blocking despite the UA pool.
BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


def _build_headers() -> dict[str, str]:
    return {**BROWSER_HEADERS, "User-Agent": random.choice(USER_AGENT_POOL)}


def _build_client(settings: Settings) -> httpx.Client:
    if not settings.reddit_http_proxy:
        raise RuntimeError(
            "APFUN_REDDIT_HTTP_PROXY is required for Reddit ingestion. "
            "Reddit blocks datacenter IPs and non-browser UAs; this project "
            "uses a residential proxy + browser-mimicking UA pool to access "
            "the public JSON endpoints. Set the env var to "
            "http://user:pass@host:port. See CLAUDE.md → Networking."
        )
    return httpx.Client(
        proxy=settings.reddit_http_proxy,
        timeout=httpx.Timeout(30.0),
        # Note: headers set per-request via _build_headers() so UA rotates
        # across requests within the same client lifetime.
    )
```

Module-level `_BUCKET = TokenBucket(rate_per_sec=3.5, burst=5)` stays unchanged.

Each Reddit HTTP call sets headers via `_build_headers()` so the UA rotates per request rather than per-client. With rotating residential IPs, this means each request looks like a different Chrome user from a different residential connection — which is just normal Reddit web traffic.

## Tests

- Unit tests for `_build_client` fail-loud on missing proxy URL.
- Unit test for `_build_headers`: returned dict has all `BROWSER_HEADERS` keys, plus `User-Agent` from the pool. Run it N times (10+), assert all three UAs appear (probabilistic but tight enough that flakes are rare).
- Unit test that outbound requests in the ingester include the full browser header set (mock the httpx client, assert headers on call args).
- Existing tests for the anonymous-path content parsing, dedup, content hash, deletion tagging — restore from pre-005b.
- Schema contract test — restore from pre-005b; should pass unchanged against existing fixtures since the JSON shape is identical.
- Integration test — gated on `APFUN_REDDIT_HTTP_PROXY` env var being set. Hits one subreddit through the proxy with the browser UA; asserts ≥1 row inserted; captures a fresh fixture if needed.
- Remove any tests that assert PRAW-style UA format. Replace with the rotation + browser-headers checks.

## Documentation updates (same PR per the docs-update convention)

1. **`CLAUDE.md → Networking`:**
   - Remove the OAuth section added in task 005b.
   - Remove any prior text about PRAW-style UA being required (introduced in feedback 012). That convention applied to authenticated OAuth access; anonymous JSON access has different requirements.
   - Add: "Reddit ingestion has two independent gating layers and requires both to be addressed: (1) Network: datacenter IPs are blocked at the network layer — requires residential proxy via `APFUN_REDDIT_HTTP_PROXY`. (2) Application: the web frontend filters non-browser UAs since June 2025 — requires browser-mimicking UA pool + full browser header set, not the PRAW-style self-identifying UA appropriate for authenticated API access. Both are implemented in `apfun/sourcing/reddit.py`."

2. **`.env.example`:**
   - Remove `APFUN_REDDIT_CLIENT_ID` and `APFUN_REDDIT_CLIENT_SECRET`.
   - Remove `APFUN_REDDIT_USERNAME` (no longer needed — PRAW-style UA is gone).
   - Add `APFUN_REDDIT_HTTP_PROXY=http://username:password@p.webshare.io:80` with comment pointing at residential-proxy signup.

3. **`docs/operator/SETUP.md`** (or whichever has operator setup):
   - Remove the Reddit app registration section from task 005b.
   - Remove any "set your Reddit username" instruction.
   - Add a section on residential proxy setup: pick a provider → grab proxy URL → set env var → restart container. Note that browser-mimicking UAs are handled internally (no operator config).

4. **`docs/operator/runbooks/003-reddit-proxy-first-pass.md`** — new runbook for the post-PR empirical test (see "Empirical validation" below).

5. **`docs/tasks/000-overview.md`:**
   - Add task 005c entry: "Reddit residential-proxy + browser-UA migration (post-005b reversal after Reddit API and frontend policy changes)."

6. **`docs/tasks/005-reddit-ingester.md`** — update footer to point at 005c instead of 005b.

7. **`docs/tasks/005b-reddit-oauth.md`** — add a header note: "ABANDONED. Reddit closed self-service OAuth in November 2025 under the Responsible Builder Policy. See 005c for the current Reddit access path."

8. **`docs/orchestrator/INDEX.md`** — row 021 → answered after PR merges.

9. **`CLAUDE.md → Lessons Learned`** (dated 2026-05-22):

   > **External-API policy changes can invalidate engineering effort mid-project, and one symptom can mask multiple causes.** Reddit's November 2025 Responsible Builder Policy made the OAuth migration from task 005b obsolete. Investigation of the workaround surfaced a *second* independent block: Reddit's web frontend started filtering by User-Agent in June 2025, meaning the PRAW-style UA convention (correct for authenticated API access) is wrong for the anonymous JSON path. The 403s the operator saw from runbook 001 onward had two causes, not one. Single-cause diagnostic mental models can miss layered defenses; verify each layer independently when a workaround unblocks the first symptom. The discipline: web-search the current state of any third-party API's access policy *and* anti-bot posture before drafting a migration spec, regardless of how confident the prior model is in its existing knowledge.

## Empirical validation (runbook 003)

After the PR merges, the operator runs `docs/operator/runbooks/003-reddit-proxy-first-pass.md`. Required steps:

1. Sign up at Webshare (or chosen provider), get proxy URL.
2. Set `APFUN_REDDIT_HTTP_PROXY` in `/srv/claude/apfun.online/.env`. Restart container.
3. Run a small ingest: `uv run python -c "from apfun.sourcing.reddit import ingest, ingest_batch; ..."` against r/SaaS.
4. Capture the artifacts:
   - HTTP status distribution: 200s, 401s, 403s, 429s
   - Row count inserted
   - Estimated cost (proxy bandwidth usage)
   - Anything operationally surprising

Three possible outcomes — they route the next move:

- **Proxy works, content flows.** Best case. Open a brief request 022 with the artifacts; close the loop; mark task 005c done; Reddit sources go back into rotation.
- **Proxy blocked.** Reddit detects the proxy IP pool. Try alternate provider (IPRoyal as fallback). If that also fails, accept that Reddit-from-this-server is not viable and disable Reddit sources permanently in seed_sources.py.
- **Partial success.** Some requests through, others blocked. Surface the pattern in request 022 — could be rate-limit tuning, could be a flaky proxy pool.

Cost budget for the runbook session: $5 maximum. At Webshare free tier, the test costs nothing; if you've upgraded to $3.50/month, that's the entire month covered.

## What I would do next without intervention

1. Branch `feature/task-005c-reddit-proxy`.
2. Revert task 005b commits cleanly. If 005b is merged: `git revert` the merge commit. If 005b is on an open feature branch: close that branch without merging.
3. Restore the anonymous-path JSON-endpoint implementation from pre-005b git history as a starting point. **Don't keep the pre-005b UA strategy** — it used PRAW-style which is now wrong for this code path.
4. Add the proxy env var + `_build_client` shape per the spec above.
5. Replace the single PRAW-style UA constant with `USER_AGENT_POOL` (3 recent Chrome UAs) and `BROWSER_HEADERS` (the full header set). Wire `_build_headers()` to be called per-request.
6. Drop `APFUN_REDDIT_USERNAME` everywhere — settings field, validator, `.env.example`, conftest fixture, any test that sets it.
7. Update tests: restore pre-005b anonymous-path tests, add UA-rotation test, add browser-headers-on-request test, remove OAuth-specific tests, remove PRAW-UA-format tests.
8. Update `scripts/capture_reddit_fixture.py` to use the proxy + browser UA path.
9. Apply documentation updates 1-9 in the same PR.
10. Write the runbook 003 file (the operator will execute it post-merge).
11. Verify `grep -r '# TODO verify' apfun/sourcing/reddit.py` returns zero.
12. Verify no references to `APFUN_REDDIT_USERNAME` or `reddit_username` remain anywhere in code, tests, docs, or `.env.example`.
13. Open PR. Note in description: "supersedes task 005b which is no longer viable due to Reddit's Nov 2025 API access policy and June 2025 UA filtering."

## Specific questions or risks

1. **Is task 005b currently merged or on an open branch?** Affects whether to `git revert` (if merged) or close-without-merge (if open). Implementer can check via `git log main --oneline | grep 005b`. If merged, the revert is part of the PR; if not, just abandon that branch.

2. **Proxy URL format edge cases.** Some providers use port forwarding (e.g., Webshare assigns one IP per port: `p.webshare.io:8000`, `:8001`, etc.). Our env var accepts a single URL, which works for any single proxy. Multi-port rotation isn't supported; operator picks one port. Document this in CLAUDE.md → Networking if it's not obvious.

3. **Should the proxy be applied to the `capture_reddit_fixture.py` script too?** Yes — the script must use the same code path as production. Otherwise fixtures captured during development won't reflect real proxy + browser-UA behavior.

4. **Container env var loading.** The operator already has `.env` next to docker-compose.yml from prior credentials work. Adding `APFUN_REDDIT_HTTP_PROXY` follows the same pattern; container restart picks it up. No Dockerfile changes needed.

5. **UA pool refresh cadence.** The 3 Chrome UAs in `USER_AGENT_POOL` should track current stable Chrome versions. Stale UAs (more than a year behind) start looking suspicious. Inline comment notes "update every 6-12 months." No automation needed for v1; treat as a manual maintenance item that surfaces if Reddit starts blocking again.

6. **What about other anti-bot signals beyond UA + headers?** Real browsers also send TLS fingerprints (JA3), do JavaScript challenges, persist cookies, etc. We're not handling any of those. The bet is that Reddit's web frontend treats UA + header constellation as "good enough" for low-volume traffic from residential IPs. If runbook 003 shows blocks despite the UA + headers + proxy, the next escalation is a JS-capable client (Playwright) — but that's task 005d, not part of this PR. Don't pre-build it.

## Relevant files

Code under change:
- `apfun/sourcing/reddit.py` — revert OAuth, add proxy support, swap UA strategy
- `apfun/config.py` — remove OAuth fields, remove `reddit_username`, add proxy field
- `tests/unit/test_reddit_ingester.py` — restore pre-005b tests, add UA-rotation + browser-headers tests, remove OAuth-specific and PRAW-UA-format tests
- `tests/integration/test_reddit_live.py` — update for proxy + browser UA path
- `scripts/capture_reddit_fixture.py` — update for proxy + browser UA path
- `tests/conftest.py` — remove `APFUN_REDDIT_USERNAME` setup if present

Docs under change (same PR):
- `CLAUDE.md` — Networking section + Lesson Learned entry
- `.env.example` — remove OAuth + username fields, add proxy field
- `docs/operator/SETUP.md` — swap Reddit registration steps for proxy setup
- `docs/operator/runbooks/003-reddit-proxy-first-pass.md` — new runbook
- `docs/tasks/000-overview.md` — task 005c cross-cutting entry
- `docs/tasks/005-reddit-ingester.md` — footer points at 005c
- `docs/tasks/005b-reddit-oauth.md` — header note marking abandoned
- `docs/orchestrator/INDEX.md` — row 021 → answered

## Meta note

The orchestrator pattern caught two policy changes one task too late — request 020 didn't search for current state. This amended 021 catches them together: the Nov 2025 Responsible Builder Policy (which killed OAuth) and the June 2025 web-frontend UA filtering (which had been quietly biting us in runbook 001 results without our recognizing it). The 403s from runbook 001 were *plausibly* the UA issue alone, even before the OAuth attempt; we didn't trace far enough back.

For future external-API tasks, the orchestrator commits to:

1. Web-searching current API access policy *and* current anti-bot/UA filtering posture before drafting any migration spec.
2. When a workaround unblocks the first observable symptom, verify whether other independent blocks exist before declaring success.

The implementer commits to surfacing any unexpected registration-flow blocks or 4xx patterns immediately rather than working around them silently.

This is a real improvement to the discipline. The cost of one wasted task (005b) and one mid-spec correction (021 amendment) is the tuition for the lesson.
