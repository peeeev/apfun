# Request 002: pre-task-002 DB foundations checks

**Date:** 2026-05-18

**Context**: Task 001 landed (commit `2f76b51`). FastAPI app boots on 0.0.0.0:4000, `/healthz` returns `{"ok": true}`, `pydantic-settings` validator rejects localhost, full quality gate (ruff + pyright strict + uv sync) clean. Pushed to GitHub. About to start task 002 (sync SQLAlchemy 2 + Alembic + the three Stage-1 tables).

**What I just did**: Built the scaffolding — `pyproject.toml` with hatchling and ruff/pyright config, `apfun/config.py` with the localhost-rejecting validator, `apfun/main.py` exposing `app` + `/healthz`, `apfun/__init__.py`, `.env.example`, `pyrightconfig.json` (strict on `apfun/`). Verified `curl http://0.0.0.0:4000/healthz` returns 200.

**What I would do next without intervention**: Task 002 — sync engine via stdlib `sqlite3` (per the gate-2 adjustment), `apfun/db.py` setting `PRAGMA journal_mode=WAL`, `PRAGMA synchronous=NORMAL`, `PRAGMA busy_timeout=5000` once at engine creation, the three Stage-1 models (sources, raw_signals, candidates with `decision` / `pipeline_stage` split), an Alembic baseline migration, `scripts/init_db.py`, and a small test inserting the source → raw_signal → candidate object graph.

**Specific questions or risks**:
- Pragma application: my instinct is "once at engine creation." But SQLAlchemy's pool can open new connections at any time and most SQLite pragmas are per-connection — should this be a `connect` event listener instead?
- `apfun/main.py` currently hardcodes `reload=True` inside `main()`. The dev container's `command:` line needs a stable CMD; `uvicorn` CLI is more flexible (`--workers`, `--log-level`) but I haven't committed to one. Worth pinning the canonical form now.
- No `Makefile` or task aliases yet. Every command is `uv run ...`. Friction will accumulate as we add migrate / revision / serve invocations.
- `foreign_keys=ON` is missing from my pragma list — SQLite defaults it OFF per connection, which would silently let FK violations through.

**Relevant files/diffs**:
- commit `2f76b51` (task 001)
- `apfun/main.py`, `apfun/config.py`, `pyproject.toml`, `pyrightconfig.json`
