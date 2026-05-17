# 001 — Project scaffolding

**Goal:** A `uv`-managed Python project with a FastAPI app that responds on `0.0.0.0:4000`, plus formatting / linting / type-checking baselines.

**Complexity:** M

## Deliverables
- `pyproject.toml` with `apfun` package metadata, Python ≥3.11, deps: `fastapi`, `uvicorn[standard]`, `httpx`, `pydantic-settings`, `jinja2`, `python-multipart`.
- Dev deps: `ruff`, `pyright`, `pytest`.
- `uv.lock` committed.
- `.gitignore` (Python, `data/`, `.venv/`, `.env`, `.ruff_cache`, `__pycache__`).
- `.env.example` with `APFUN_*` placeholders (no real secrets).
- `apfun/__init__.py`, `apfun/main.py` exposing `app` and a `GET /healthz` returning `{"ok": true}`.
- `apfun/config.py` using `pydantic-settings`, env prefix `APFUN_`.
- `ruff.toml` or `[tool.ruff]` config; `pyrightconfig.json` strict on `apfun/`.
- README is NOT required this task — skip unless asked.

## Run command (documented in task, not auto-run)
`uvicorn apfun.main:app --host 0.0.0.0 --port 4000 --reload`

## Acceptance
- `uv sync` succeeds clean.
- `ruff check apfun/` and `pyright apfun/` pass.
- Inside the dev container, `curl http://0.0.0.0:4000/healthz` from another shell returns `{"ok": true}` once the app is running.
- No `Dockerfile` or `docker-compose.yml` is created anywhere in `/workspace`.

## Notes
- Binding to `127.0.0.1` is a regression — guard against it in `apfun/main.py` (read host/port from settings, default `0.0.0.0`/`4000`).
- Add `--reload` only to the documented dev command, not to a production launcher.
