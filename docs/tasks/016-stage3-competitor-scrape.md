# 016 — Stage 3 competitor scraping

**Goal:** For an approved candidate, identify the top competitors (from SERP + "alternatives to" queries) and capture pricing, features, and recent funding for each.

**Complexity:** L

Depends on: 009, 015.

## Deliverables
- `apfun/pipeline/competitive.py`:
  - From candidate seed keywords: call `serp_top10` and `related_keywords` to assemble a competitor shortlist (top 5; configurable).
  - For each competitor URL: fetch via `httpx`, then if necessary Playwright (heuristic: missing pricing keywords → JS render).
  - Extract pricing tiers using a Haiku `mechanic("extract_pricing", ...)` call given the page text. Then **always** spot-check with `judge("review_pricing", ...)` on the top result — see §10: "LLMs hallucinate competitor features from SERP snippets."
  - Extract feature list similarly.
  - For funding/news: query the top result on `https://www.crunchbase.com/organization/<slug>` (no API; HTML parse) and recent press via a DataForSEO SERP query `<competitor> raised OR funding`. Capture amounts + dates.
  - Reviews are NOT fetched here — task 017 handles deep review mining for the top-3 competitors.
  - Persist a `competitive_analyses` row per competitor.

## Acceptance
- Integration test (network-gated, opt-in) on one known candidate produces 3–5 `competitive_analyses` rows with non-empty `pricing_json` and `features_json`.
- Unit tests on the extractor functions using saved HTML fixtures.

## Notes
- Don't sample pricing from SERP snippets alone — that's a known hallucination trap.
- If a competitor blocks scraping, write the row with `scraped_at` and a `notes` field explaining the gap; don't drop them silently.
