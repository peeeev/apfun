# 002 — DB foundations

**Goal:** SQLAlchemy 2.x async + Alembic wired up, with the three Stage-1 tables.

Depends on: 001.

## Deliverables
- Deps: `sqlalchemy[asyncio]`, `aiosqlite`, `alembic`.
- `apfun/db.py`: async engine (URL from settings, default `sqlite+aiosqlite:///data/apfun.db`), `async_sessionmaker`, FastAPI dependency `get_session`.
- `apfun/models/base.py`: `DeclarativeBase` with `id`/`created_at`/`updated_at` mixins.
- `apfun/models/source.py` — `sources` table (see data model below).
- `apfun/models/raw_signal.py` — `raw_signals` with unique `content_hash`.
- `apfun/models/candidate.py` — `candidates` + `candidate_signals` junction.
- `migrations/` configured for SQLite, env wired to async metadata.
- First Alembic revision applying all three tables.
- `scripts/init_db.py` — convenience: create `data/`, run `alembic upgrade head`.

## Tables (this task)
- `sources(id, kind, name, config_json, is_active, last_fetched_at, last_error)`
- `raw_signals(id, source_id, external_id, url, captured_at, content_hash UNIQUE, payload_json, processed_at, vertical)`
- `candidates(id, problem_statement, suspected_user, seed_keywords_json, vertical, dedup_key, status, created_at)`
- `candidate_signals(candidate_id, raw_signal_id, PRIMARY KEY (both))`

`status` enum on `candidates`: `pending_demand`, `pending_review`, `approved`, `rejected`, `auto_killed`.

## Acceptance
- `python scripts/init_db.py` creates `data/apfun.db` with all four tables.
- `alembic downgrade base && alembic upgrade head` round-trips without error.
- Unit test inserts a `source`, a `raw_signal` referencing it, a `candidate`, and links them via `candidate_signals`.
