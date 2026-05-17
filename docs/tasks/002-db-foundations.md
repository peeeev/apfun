# 002 — DB foundations

**Goal:** SQLAlchemy 2.x sync + Alembic wired up, with the three Stage-1 tables.

**Complexity:** M

Depends on: 001.

## Deliverables
- Deps: `sqlalchemy`, `alembic`.
- `apfun/db.py`: sync engine (URL from settings, default `sqlite:///data/apfun.db`), `sessionmaker`, FastAPI dependency `get_session`. An `@event.listens_for(engine, "connect")` hook applies `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA busy_timeout=5000` on every new connection.
- `apfun/models/base.py`: `DeclarativeBase` with `id`/`created_at`/`updated_at` mixins.
- `apfun/models/source.py` — `sources` table (see data model below).
- `apfun/models/raw_signal.py` — `raw_signals` with unique `content_hash`.
- `apfun/models/candidate.py` — `candidates` + `candidate_signals` junction.
- `migrations/` configured for SQLite, env wired to the sync metadata.
- First Alembic revision applying all three tables.
- `scripts/init_db.py` — convenience: create `data/`, run `alembic upgrade head`.

## Tables (this task)
- `sources(id, kind, name, config_json, is_active, last_fetched_at, last_error)`
- `raw_signals(id, source_id, external_id, url, captured_at, content_hash UNIQUE, payload_json, processed_at, vertical)`
- `candidates(id, problem_statement, suspected_user, seed_keywords_json, vertical, dedup_key, decision, pipeline_stage, created_at)`
- `candidate_signals(candidate_id, raw_signal_id, PRIMARY KEY (both))`

`candidates.decision` enum: `pending`, `approved`, `rejected`, `auto_killed` (default `pending`). Owned by the HITL gate.

`candidates.pipeline_stage` enum: `none`, `competitive`, `scoring`, `synthesizing`, `done`, `failed` (default `none`). Owned by the Stage 3→5 orchestrator (task 019).

The two columns are independent: Stage 2 may flip `decision='auto_killed'` while `pipeline_stage='none'`; HITL rejection leaves `pipeline_stage='none'`; a pipeline failure can coexist with `decision='approved'`. "Stage 2 has run" is implicit via the presence of a `demand_checks` row; the HITL-inbox query (task 014) is `decision='pending' AND latest demand_checks.verdict='pass'`.

## Acceptance
- `python scripts/init_db.py` creates `data/apfun.db` with all four tables.
- `alembic downgrade base && alembic upgrade head` round-trips without error.
- A fresh connection has WAL active: `PRAGMA journal_mode;` returns `wal`.
- Unit test inserts a `source`, a `raw_signal` referencing it, a `candidate` (defaulting to `decision='pending'`, `pipeline_stage='none'`), and links them via `candidate_signals`.
