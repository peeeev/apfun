# Request 014: task 009 prep — container update + review-miner architecture

**Date:** 2026-05-19

**Context**: Task 008 (IndieHackers) merged via PR #5. With four ingesters now sharing the `apfun/sourcing/_base` skeleton, the patterns are stable. Task 009 (review miner — G2 / Capterra / Trustpilot) is **L complexity** and the first one that genuinely diverges from the existing shape: headless-browser-based scraping rather than HTTP-JSON, per-site adapter modules, anti-scraping concerns real enough that the spec includes a CSV manual-import fallback as a co-deliverable. Surfacing the design + ops questions before any code lands.

## What landed in task 008

(The orchestrator can't see the PR directly; summarizing here.)

### IndieHackers ingester (merged at `c7bd9f0`)

- **`apfun/sourcing/indiehackers.py`** — fetches the grouppage HTML, parses the Next.js `__NEXT_DATA__` JSON blob when present, falls back to selectolax HTML scraping of post cards when not. On both-paths-fail, returns `IngestResult(error_class="parse_error")` rather than raising. content_hash = `sha256(post_url)`. `TokenBucket(rate_per_sec=1.0, burst=2)`. `TERMINAL_STATUSES = {403, 404}` (Cloudflare-typical block / renamed group); 429 is transient and goes through the retry loop. Payload tagged with `_apfun_group` + `_apfun_url`.
- **Inline retry** rather than `_base.run_with_retry` because IH returns HTML (not JSON). Same `MAX_RETRIES` / `RETRY_BASE_DELAY_S` constants from `_base`. The "extract retry helper for the HTML case" is deferred — only one HTML source so far; the right shape becomes clearer if task 009 needs the same pattern.
- **Parse failures don't increment `consecutive_failures`** in the batch wrapper — the source itself was reachable (200), the issue is layout drift on our side. Surfaces via `scheduler_runs`; doesn't auto-disable.
- **Cloudflare risk acknowledged**: integration test surfaces a clear "park IH and re-prioritize task 009" message if blocked rather than fighting the block.
- **Tests**: 4 schema contract + 12 ingester unit + 5 batch wrapper + 1 integration (gated). 20 new tests; 116 total unit tests now pass.
- **`selectolax`** added as a new dep for HTML parsing.

**TokenBucket abstraction held for the fourth consumer** — `1.0/s, burst 2` for IH alongside Reddit (3.5/s, burst 5), HN (1.5/s, burst 3), PH (1.0/s, burst 2). No abstraction escalation needed.

## Container readiness (blocker — needs your action before task 009 starts)

Task 009 needs `playwright` + a Chromium binary + Chromium's system-library dependencies. I checked the current container:

```
# /workspace dev container is Debian 12 (bookworm)
$ dpkg -l | grep -E 'libnss3|libgbm|libxshmfence|chromium'
(no results)
```

None of the runtime deps are installed. Per CLAUDE.md §Directory boundaries I cannot author or modify the host `Dockerfile`/`docker-compose.yml` (they live at `/srv/claude/apfun.online/`, outside `/workspace`).

**Ask**: please update the host `Dockerfile` to install Chromium's runtime deps plus a Chromium binary. The minimal apt list for playwright/Chromium on Debian 12:

```
libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0
libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2
libasound2 libatspi2.0-0 libwayland-client0
```

Or — the operationally simpler shape — let Playwright install its own Chromium with bundled deps:

```dockerfile
RUN pip install playwright && playwright install --with-deps chromium
```

`--with-deps` is the magic flag; it apt-installs the runtime libraries it knows it needs. Slightly larger image but zero hand-curated library lists.

Once the container is rebuilt, `which chromium` (or playwright's bundled path) returns a binary and I can proceed.

## Architecture questions

### Q1 — `BrowserContext` lifecycle: where does it live?

Existing ingesters work like this: `ingest(session, source, client: httpx.Client | None)` — the batch wrapper creates one `httpx.Client` and passes it to every per-source `ingest()`. Lightweight, stateless across sources.

Playwright is different. The lifecycle is:

```python
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(...)  # holds cookies/storage
    page = context.new_page()
    page.goto(...)
    ...
    context.close()
    browser.close()
```

The spec says "persistent browser context to share cookies across pages." That implies one `context` per `ingest()` invocation (covers all the configured products), or maybe one `context` per **site** across all products of that site.

**My lean**: one `BrowserContext` per ingest call, scoped to one source. Reasons:
- Cookies relevant to a site stay scoped naturally
- Memory usage is bounded — context closes when ingest finishes
- Failure isolation: if G2's context dies, Capterra's is unaffected
- Mirrors httpx.Client lifecycle: one per ingest call

**Reject**: a global module-level `BrowserContext` (memory growth, cross-site cookie leakage, harder to test).

Confirm or push back.

### Q2 — Adapter pattern: three modules or one with dispatch?

Spec says `apfun/sourcing/review_sites.py` with per-site adapters `g2.py`, `capterra.py`, `trustpilot.py`.

Two readings:

- **(a)** Three top-level modules: `apfun/sourcing/g2.py`, `apfun/sourcing/capterra.py`, `apfun/sourcing/trustpilot.py`. Each has its own `ingest()` / `ingest_batch()` using `_base`. Pattern matches Reddit/HN/PH/IH — one module per source kind.

- **(b)** One umbrella module `apfun/sourcing/review_sites.py` that holds shared review-mining logic (browser context lifecycle, dedup on `(site, product_slug, review_id)`, helpful-count extraction), plus per-site adapter sub-modules `apfun/sourcing/review_sites/g2.py` etc. that just implement `fetch_reviews(context, product_slug, max_pages) -> list[ReviewDict]`. One source-kind in the DB (`"review"`); `source.config_json["site"]` discriminates.

**My lean**: **(b)**. Reasons:
- All three sites share the same review-mining concepts (rating, title, body, author, posted_at, helpful_count). The differences are in CSS selectors and pagination.
- One ingester managing one browser context across three sites' worth of products is operationally cleaner.
- DB schema-wise, "site=g2, product=asana" is a per-source detail in `config_json` — not three separate `kind` values.
- The adapter functions are short (per spec, just `fetch_reviews(slug, max_pages) -> list[ReviewDict]`); making each into a full top-level ingester module is overkill.

Trade-off: site-specific failures (e.g., G2 anti-bot harder than Trustpilot) might want per-site `is_active` toggling. But that's already supported because each *source row* is per-site (`config_json["site"] = "g2"`), so disabling a G2 source row leaves Trustpilot sources alone.

Confirm **(b)** or push back to **(a)**.

### Q3 — `_base.run_ingest_batch` integration: does it fit?

`run_ingest_batch` currently takes `client: httpx.Client | None`. With Playwright, the equivalent is `BrowserContext`. Two options:

- **(b1)** Generalize `client` to `Any` (or use a Protocol). Loses some type safety but no per-source-type batch helpers.
- **(b2)** Write a separate `apfun/sourcing/_base.run_browser_ingest_batch(...)` that accepts a `BrowserContext` factory instead. Same outer shape (per-source try/except, scheduler_runs, etc.) but typed for the browser case.

**My lean**: **(b1)** with a Protocol. Define:

```python
class IngestClient(Protocol):
    def close(self) -> None: ...
```

`httpx.Client` already satisfies that. For Playwright we can wrap `BrowserContext` in a thin `BrowserClient` shim that has a `.close()` method and exposes the underlying context to per-source ingest functions. Keeps `run_ingest_batch` source-agnostic.

The shim probably looks like:

```python
@dataclass
class BrowserClient:
    context: BrowserContext
    def close(self) -> None:
        self.context.close()
        self.context.browser.close()  # or whatever cleanup is needed
```

Confirm direction, or suggest a different abstraction.

### Q4 — CSV import fallback: same PR or separate?

Spec includes `scripts/import_reviews.py` as a deliverable: "If a site repeatedly fails, fall back to manual CSV import." Implies it's tested + working from day one, not a "we'll get to it" stub.

The CSV import is logically independent from the scraping path: it reads a CSV → inserts `raw_signals` with the same payload shape. Could land as a separate PR.

**My lean**: **same PR**. Reasons:
- The deliverable is bundled in task 009
- Both code paths share the row-insert shape; co-developing them keeps them aligned
- Operationally, you want to be *able* to fall back the moment scraping breaks, not "wait for the next PR"

Alternative: ship scraping first, follow up with CSV import in a separate small PR if scraping turns out to be straightforward. Probably *not* preferable given anti-scraping risk.

Confirm same PR.

### Q5 — content_hash: `(site, product_slug, review_id_or_perma)` — what's the perma fallback?

Spec says hash on `(site, product_slug, review_id_or_perma)`. G2 reviews have a numeric ID in their permalink (`/g2/asana/reviews/<id>`); Capterra similar; Trustpilot uses opaque ID strings. So `review_id` is usually present.

When it isn't (e.g., a review with no anchor link), what's the perma?

**My lean**: fall back to `sha256(site || product_slug || rating || posted_at || author || body[:200])`. The hash itself becomes the "perma" — same review never produces different hash. Less stable than a real ID (a review-edit changes body, changing the hash), but better than dropping the row.

Alternative: skip the row entirely. Cleaner dedup, but means review-edits across runs produce duplicate rows. Bad.

Confirm fallback approach.

### Q6 — Polite delays + anti-scraping posture

`TokenBucket(rate_per_sec=?, burst=?)` for review sites — what's the conservative-but-not-insulting default?

**My lean**: 0.5 req/s sustained, burst 2. Across G2/Capterra/Trustpilot all served from one bucket per ingest call (cookies sharing). Add a 1-3s `time.sleep` randomization between page-fetches to look less robotic. Annotate as `# heuristic` — none of these sites publish rate limits.

Confirm or suggest a different starting point.

## Specific questions in order of priority

1. **Container update** (Q0): please update the host Dockerfile to include playwright + chromium-with-deps. This is the blocker. Once landed, I can `uv add playwright` and verify the binary launches before any task 009 code goes in.
2. **Architecture (Q1–Q3)**: confirm one `BrowserContext` per ingest call, single umbrella module with per-site adapters, `IngestClient` Protocol in `_base` for the generalization.
3. **Scope (Q4)**: confirm CSV import lands in the same PR.
4. **Dedup (Q5)**: confirm the body-hash fallback for missing review_ids.
5. **Rate limit (Q6)**: confirm 0.5/s sustained, burst 2, plus per-page jitter.

## What I would do next without intervention

Once Q0 (container) and Q1–Q6 (design) are answered, expected implementation order on this same `feature/task-009-review-miner` branch:

1. Single prep commit applying any feedback adjustments + the `IngestClient` Protocol generalization in `_base.py` (test-only diff — behavior preserved for existing four ingesters).
2. `scripts/setup_playwright.py` one-off setup script (`playwright install chromium` invocation; idempotent).
3. `apfun/sourcing/review_sites/__init__.py` + the three per-site adapter modules + the umbrella `ingest`/`ingest_batch`. Browser-context shim in `_base` or a new `apfun/sourcing/_browser.py`.
4. `scripts/import_reviews.py` CSV path + unit tests against captured CSVs.
5. Per-site fixture captures via `scripts/capture_*_fixture.py` scripts (manual run on real product pages once playwright is installed).
6. Unit tests against the saved HTML captures + opt-in integration test that hits one page per site.
7. `seed_sources.py` extension with a handful of tracked products.

Expected commit count: 1 prep + 1 review_sites scaffold + 1 per-site adapter (×3) + 1 CSV import + 1 tests/seeds = ~7 commits.

## Relevant files/diffs

- main at `c7bd9f0` (task 008 merged)
- this branch `feature/task-009-review-miner` (currently just contains this request)
- `docs/tasks/009-review-miner.md` — current spec
- `apfun/sourcing/_base.py` — the shared skeleton from PR #4 that task 009 will extend
- `docs/orchestrator/INDEX.md` — row 014 → open
