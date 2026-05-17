# 003 — DB pipeline tables

**Goal:** Remaining tables for Stages 2–6 plus observability.

**Complexity:** M

Depends on: 002.

## Tables
- `demand_checks(id, candidate_id FK, run_at, trend_slope, autosuggest_json, verdict, notes)` — `verdict` ∈ `pass`/`fail`.
- `approvals(id, candidate_id FK, decision, comment, decided_at)` — `decision` ∈ `approve`/`reject`. (No user FK; single-user system.)
- `competitive_analyses(id, candidate_id FK, competitor_name, competitor_url, pricing_json, features_json, funding_json, reviews_summary_json, scraped_at)`
- `scores(id, candidate_id FK, demand, supply, unmet_pain, moat_potential, composite, breakdown_json, scored_at, model_version)`
- `opportunities(id, candidate_id FK UNIQUE, top_complaints_json, feature_gaps_json, pricing_gaps_json, vertical_wedge, sources_json, synthesized_at, status)` — `status` ∈ `active`/`archived`/`built`.
- `projects(id, opportunity_id FK, slug, subdomain, status, created_at)` — `status` ∈ `placeholder`/`in_dev`/`live`/`sunset`.
- `llm_runs(id, task, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, latency_ms, est_cost_usd, candidate_id NULLABLE FK, created_at, ok, error)` — every Anthropic call goes here.
- `scheduler_runs(id, job_id, started_at, finished_at, ok, error, items_processed)` — one row per scheduled job firing.
- `api_usage(id, provider, day DATE, est_cost_usd, calls, payload_json)` — for DataForSEO budget cap (provider == `dataforseo`); extensible.

## Deliverables
- One Alembic revision adding all tables above.
- Models under `apfun/models/`, one file per aggregate.
- JSON columns use `sqlalchemy.JSON` (portable across SQLite/Postgres).
- Indices: `scores(candidate_id)`, `competitive_analyses(candidate_id)`, `llm_runs(task, created_at)`, `api_usage(provider, day)` unique.

## Acceptance
- Migration round-trips up/down.
- Unit test creates a full happy-path object graph: candidate → demand_check → approval → competitive_analysis → score → opportunity → project, and reads them back.
