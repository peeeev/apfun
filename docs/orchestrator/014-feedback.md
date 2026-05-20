# Feedback 014 — task 009 prep: playwright container + review-miner design

**Date:** 2026-05-19
**Request:** 014-task-009-prep.md
**Outcome:** Dockerfile update attached for you to apply (blocker resolved). Six design confirmations with refinements. Task 009 risk profile flagged.

## Blocker — host Dockerfile update

Use `playwright install --with-deps chromium`. Hand-curated apt lists silently rot when Chromium version-bumps add transitive deps; `--with-deps` lets Playwright manage what it knows it needs.

**Lines added to `/srv/claude/apfun.online/Dockerfile`** (full file attached as `Dockerfile`):

```dockerfile
# ───── Playwright + Chromium (for task 009 review miner) ─────
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN pip install --break-system-packages playwright \
    && playwright install --with-deps chromium \
    && mkdir -p /ms-playwright \
    && chown -R node:node /ms-playwright
```

Placement: before `USER node`. `pip install --break-system-packages` is required because Debian 12's system Python is PEP 668-managed. `PLAYWRIGHT_BROWSERS_PATH=/ms-playwright` puts browser binaries somewhere the node user can read (default would be `/root/.cache/ms-playwright/`, inaccessible).

**To apply on the host:**

```bash
# Stop the current container, replace Dockerfile, rebuild
cd /srv/claude/apfun.online
docker compose down
# Replace Dockerfile with the attached version
docker compose up -d --build
docker exec -it apfun-funnel bash
# Verify inside the container:
playwright --version
python3 -c "from playwright.sync_api import sync_playwright; print('ok')"
```

Both should succeed. Image will be ~700 MB larger; acceptable.

## Design confirmations

### Q1 — BrowserContext lifecycle: confirmed, with refinement

**One Browser per batch, one Context per source.** Your reasoning is sound; the refinement is to recognize that browsers are expensive to launch (~1-2s), contexts are cheap (~50ms). Across G2 + Capterra + Trustpilot in one batch, that's ~1.5s vs ~4.5s of overhead, compounding with multiple products per site.

Shape:

```python
def run_browser_ingest_batch(session, sources, ...):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            for source in sources:
                context = browser.new_context(...)
                try:
                    result = ingest(session, source, context)
                finally:
                    context.close()
        finally:
            browser.close()
```

Browser scope = batch; context scope = source. Memory + isolation guarantees without paying browser-launch cost per source.

### Q2 — Single umbrella module with per-site adapters: confirmed (b)

Concrete shape:

```
apfun/sourcing/review_sites/
├── __init__.py
├── _common.py           # ReviewDict, dedup, ingest(), ingest_batch()
├── g2.py                # def fetch_reviews(context, slug, max_pages) -> list[ReviewDict]
├── capterra.py          # same signature
└── trustpilot.py        # same signature
```

`source.config_json["site"]` discriminates; `_common.ingest()` dispatches.

**Decide flat-vs-directory based on file count once they exist.** If it's just four small files, a flat layout (`review_sites.py` + `_review_g2.py` etc.) is also fine. Default to the directory shape since the spec named it. Don't over-think before writing.

### Q3 — IngestClient Protocol with concrete shim: confirmed

Protocol for batch-level lifecycle, concrete types where they help:

```python
from typing import Protocol

class IngestClient(Protocol):
    """Batch-level lifecycle contract."""
    def close(self) -> None: ...
```

`httpx.Client` already satisfies. For the browser case, define a thin batch-client:

```python
@dataclass
class BrowserBatchClient:
    """Batch-level client for browser-based ingesters. Holds the playwright
    Browser; per-source ingest() creates contexts as needed."""
    playwright: "Playwright"
    browser: "Browser"

    def new_context(self, **kwargs) -> BrowserContext:
        return self.browser.new_context(**kwargs)

    def close(self) -> None:
        self.browser.close()
        self.playwright.stop()
```

Per-source `ingest(session, source, client: BrowserBatchClient)` does `with client.new_context() as ctx:` for each. Concrete types in per-source code; Protocol where it doesn't constrain (the batch wrapper's cleanup).

### Q4 — CSV import in same PR: confirmed

Three reasons stack:

1. Listed as a deliverable in the task spec — splitting weakens the meaning of "task complete."
2. Fallback's value is proportional to its availability. Half-shipped fallback defeats the purpose.
3. Row-insert path alignment: co-developing keeps the CSV and scraping paths producing identical `raw_signals` shape; a bug in one is more easily caught against the other.

**If the PR grows large:** split into two commits within the same PR (`009a: scraping path`, `009b: CSV import`). Each with tests. Reviewable in halves without losing the ship-together property.

### Q5 — Body-hash fallback for missing review IDs: confirmed

Body-hash is correct. Skip-the-row alternative creates worse failure mode (review-edits → duplicate rows). Make the trade-off explicit in code:

```python
def review_content_hash(site, product_slug, review_id, *, rating, posted_at, author, body):
    if review_id:
        return sha256(f"{site}|{product_slug}|{review_id}".encode())
    # Fallback: synthesize a stable identifier from intrinsic review attributes.
    # Note: an edited review will produce a new hash, creating a new raw_signal
    # row. That's acceptable — edits ARE a kind of new signal — and rare enough
    # not to spam the pipeline.
    body_prefix = (body or "").strip()[:200]
    return sha256(
        f"{site}|{product_slug}|{rating}|{posted_at}|{author}|{body_prefix}".encode()
    )
```

The comment in the fallback path is the load-bearing artifact — it makes the trade-off visible to future readers.

### Q6 — Rate limit: 0.5/s + burst 2 + jitter — confirmed with two additions

Baseline numbers confirmed.

**Addition 1 — UA:** use Playwright's bundled Chromium UA, **not** an apfun-identifying string. Review sites soft-block obvious bots more readily than generic Chrome traffic. Tactical exception to the "self-identifying UA" pattern from earlier ingesters; document inline:

```python
# heuristic 2026-05-19 — review sites soft-block obvious bots more
# readily than generic Chrome traffic. Use Playwright's bundled UA
# (recent stable Chrome) rather than apfun-funnel/X.Y. This is a
# deliberate exception to the self-identifying UA pattern used by
# Reddit/HN/PH/IH ingesters.
```

**Addition 2 — Bail fast on block markers:** if a response is 200 but contains Cloudflare challenge fragments or "rate limit" / "access denied" strings, treat as terminal for that source in the current batch. Don't retry — retrying with the same context won't help.

```python
CLOUDFLARE_BLOCK_MARKERS = (
    "Just a moment",
    "cf-browser-verification",
    "rate limit",
    "Access denied",
)

# heuristic 2026-05-19 — empirical markers from observing CF challenge
# pages. If we see these, scraping is blocked at the edge; retrying with
# the same context won't help. Surface in scheduler_runs and fall back
# to manual CSV import.
```

## Task 009 risk profile

This is the highest-risk task in the project. Three things worth saying explicitly:

### 1. Don't fight blocks

If G2 or Capterra blocks Playwright outright (residential IP detection, advanced fingerprinting), **the CSV import path is the answer.** Don't escalate to stealth Playwright plugins, residential proxies, etc. — that's a different project. Note this explicitly in the task 009 PR.

### 2. Capture fixtures aggressively while scraping works

Every successful page fetch is worth saving to `tests/fixtures/review_sites/`. If anti-bot ramps up next month, the test suite still works against historical captures. Treat fixtures as a hedge against future blockage.

### 3. Plan to revisit

Even if scraping works on day one, anti-bot evolves. Task 009 is "build the miner"; it's NOT "build the miner that works forever." Build it, capture fixtures, document failure modes, treat as maintenance load that gets periodic refresh.

## Action items

1. **Apply attached Dockerfile** to `/srv/claude/apfun.online/Dockerfile`. Rebuild container. Verify `playwright --version` works for the node user. (Blocker — your action; Claude Code can't write to that file.)
2. After container rebuild: `uv add playwright` in the project; commit.
3. Apply Q1–Q6 confirmations + refinements in task 009 implementation per the order in the request.
4. First implementation commit: prep commit with `IngestClient` Protocol + `BrowserBatchClient` in `_base` (or `_browser.py`). Test-only diff; existing four ingesters behavior preserved.

## Next step

Once container is rebuilt and `playwright --version` works, proceed with the implementation order from the request:

1. Prep commit (Protocol + batch-client shim)
2. `scripts/setup_playwright.py` (idempotent install verification, in case dev environments need it)
3. `review_sites/` module + adapters
4. `scripts/import_reviews.py` (CSV import)
5. Fixture captures + tests
6. Seeds extension

Expected: 7-ish commits, single PR. If the PR feels heavy at review time, split as 009a/009b suggested in Q4.

## Meta note

Q0 (container update) demonstrates the directory-boundary rule working correctly — you correctly identified what's inside vs outside your sandbox and asked rather than tried to author. That's the discipline. The "ask in chat" path is exactly what feedback 0/§0 envisioned for these cases.
