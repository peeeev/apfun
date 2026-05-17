# 009 — Review miner (G2 / Capterra / Trustpilot)

**Goal:** Pull 1–3★ reviews on a configurable set of tracked products. The most differentiation-rich source in the funnel.

**Complexity:** L

Depends on: 002.

## Deliverables
- Deps: `playwright`, `playwright install chromium` baked into a one-off setup script (task notes the host may need to update the container image — flag in chat if so).
- `apfun/sourcing/review_sites.py` with per-site adapters: `g2.py`, `capterra.py`, `trustpilot.py`. Each implements `fetch_reviews(product_slug, max_pages) -> list[ReviewDict]`.
- `source.config_json`: `{"site": "g2", "products": [{"slug": "asana", "name": "Asana"}, ...], "max_pages": 3, "min_star": 1, "max_star": 3}`.
- Reviews land in `raw_signals` with `payload_json = {site, product_slug, product_name, rating, title, body, author, posted_at, helpful_count}`.
- Content hash on `(site, product_slug, review_id_or_perma)`.
- Polite delays + persistent browser context to share cookies across pages.

## Acceptance
- Each adapter has a fixture-backed unit test using a saved HTML capture.
- Opt-in integration test fetches one page from one product per site.
- The crawl yields ≥1 row from a known tracked product in dry-run mode.

## Notes
- Anti-scraping is real here. If a site repeatedly fails, fall back to manual CSV import (`scripts/import_reviews.py`) — add that script as part of this task.
- Each review's `helpful_count` is the strongest "this matters" signal; preserve it for Stage 4 weighting.
