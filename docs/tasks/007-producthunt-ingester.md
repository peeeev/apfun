# 007 — ProductHunt ingester

**Goal:** Capture recent launches + (where possible) their negative-space signals.

Depends on: 002.

## Deliverables
- `apfun/sourcing/producthunt.py` using ProductHunt's GraphQL API (`https://api.producthunt.com/v2/api/graphql`).
- API token in env `APFUN_PRODUCTHUNT_TOKEN` (developer token, single-user). If missing, the ingester logs a warning and no-ops cleanly — don't crash.
- Pull yesterday's top posts (configurable `n_days`), capturing name, tagline, description, topics, votes_count, comments_count, url.
- `content_hash = sha256(slug)`.

## Acceptance
- Fixture test against a saved GraphQL response.
- Integration test (opt-in, requires `APFUN_PRODUCTHUNT_TOKEN`).
- Missing-token path emits a `scheduler_runs` row with `ok=true, items_processed=0` and a warning in `notes`.

## Notes
- "Negative space" (what's *missing*) is harder than what's launched — capture launches only in this task. Stage 1 clustering (task 010) is responsible for inverting them into gaps.
