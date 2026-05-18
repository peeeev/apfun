# 003 — DB pipeline tables

**Goal:** Remaining tables for Stages 2–6 plus observability.

**Complexity:** M

Depends on: 002.

## Conventions established by this task (also in CLAUDE.md)

- **Every FK column gets an explicit `index=True`.** SQLite doesn't auto-index FKs.
- **JSON columns are reassign-only.** Build the new value locally and assign it whole; don't mutate in place.

## Tables

### Core pipeline (commit A)
- `demand_checks(id, candidate_id FK, run_at, trend_slope, autosuggest_json, verdict, notes)` — `verdict` ∈ `pass`/`fail`.
- `approvals(id, candidate_id FK, decision, comment, decided_at)` — `decision` ∈ `approve`/`reject`. (No user FK; single-user system.)
- `competitive_analyses(id, candidate_id FK, competitor_name, competitor_url, pricing_json, features_json, funding_json, reviews_summary_json, scraped_at, notes)`
- `scores(id, candidate_id FK, demand, supply, unmet_pain, moat_potential, composite, breakdown_json, scored_at, model_version)`
- `opportunities(id, candidate_id FK UNIQUE, top_complaints_json, feature_gaps_json, pricing_gaps_json, vertical_wedge, sources_json, synthesized_at, status)` — `status` ∈ `active`/`archived`/`built`.
- `projects(id, opportunity_id FK, slug UNIQUE, subdomain, status, created_at)` — `status` ∈ `placeholder`/`in_dev`/`live`/`sunset`.

Also retroactive in this commit: `Index("ix_candidate_signals_raw_signal_id", "raw_signal_id")` — the composite PK on `candidate_signals` only covers the `candidate_id` left-prefix.

### Telemetry (commit B)
- `llm_runs(id, task, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, latency_ms, est_cost_usd, candidate_id NULLABLE FK, ok, error)` — every Anthropic call.
- `scheduler_runs(id, job_id, started_at, finished_at, ok, error, items_processed)` — one row per scheduled job firing.
- `api_usage(id, provider, day DATE, est_cost_usd, calls, payload_json)` — daily aggregate; unique on `(provider, day)`.

## Deliverables
- One Alembic revision per commit (two total): core pipeline tables, then telemetry tables. Both round-trip up/down.
- Models under `apfun/models/`, one file per aggregate. Enum values are stored lowercase and enforced at the DB by `CheckConstraint` in `__table_args__` (SQLAlchemy `Enum(native_enum=False)` alone does NOT emit the CHECK — the helpers in `apfun/models/base.py` build the SQL).
- JSON columns use `sqlalchemy.JSON` (portable across SQLite/Postgres). No `MutableDict` wrapping — the project rule is reassign-only.
- All FK columns are indexed (`index=True` or via a UNIQUE constraint). Composite indices where queries demand: `llm_runs(task, created_at)`, `scheduler_runs(job_id, started_at)`, `api_usage(provider, day)` UNIQUE.

## Acceptance
- Both migrations round-trip up/down.
- CHECK constraints reject bogus enum values at the DB level (verified via raw SQL test, bypassing the ORM).
- Happy-path object graph test: candidate → demand_check → approval → competitive_analysis → score → opportunity → project. Read back, defaults checked (`opportunity.status='active'`, `project.status='placeholder'`).
- Telemetry test: one `llm_run`, one `scheduler_run`, one `api_usage(provider, day)` row, all read back; the unique on `(provider, day)` blocks duplicates.
