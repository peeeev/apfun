# 017 — Stage 4 saturation scoring

**Goal:** Compute a `scores` row per approved candidate with full breakdown.

**Complexity:** M

Depends on: 016. Pulls review data populated by extending task 009's miner to also fetch for the top-3 competitors identified in 016.

## Deliverables
- `apfun/scoring/saturation.py`:
  - **Demand** = `log(volume + 1) × (1 + cpc_normalized) × growth_factor` where `growth_factor = clamp(1 + slope, 0.2, 3.0)` from `demand_checks`.
  - **Supply** (a.k.a. IncumbentStrength) = `count(competitors_with_DA≥30) × mean(DA) × log(1 + total_estimated_ad_spend)`.
  - **UnmetPain** = weighted negative-review density: for each competitor's 1–3★ reviews, ask Opus (`judge("complaint_clusters", ...)`) for top 5 complaint themes + their frequency; UnmetPain is `Σ (theme_count / total_reviews × severity_weight)`. Normalize against the category baseline so common SaaS gripes (slow support) don't dominate.
  - **MoatPotential** = a Haiku-rated 1–5 on `{technical_complexity, integration_depth, regulatory_angle}` then averaged. Cheap; this is a tie-breaker.
  - **Composite** = `(Demand × UnmetPain) / max(Supply, 1.0)`, log-transformed for display.
  - All components persisted in `scores.breakdown_json`; the four named columns hold the normalized [0,1] values; `composite` is the raw output for ranking.
- `model_version` column captures a semver of the formula so historical scores can be re-interpreted.

## Acceptance
- Unit test with synthetic inputs verifies each component's formula and the composite.
- Integration test on one candidate (with 016 already populated) produces a score with all four components > 0.
- A "kill" candidate (very low demand or very high supply) produces composite < 0.1.

## Notes
- "Saturated ≠ skip." A candidate with very high Supply AND very high UnmetPain is the most interesting case — verticalization wedge. Make sure the composite formula doesn't accidentally suppress this.
- This is the second of three Opus judgment stages. Don't reroute `complaint_clusters` to Haiku.
