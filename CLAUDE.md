# CLAUDE.md — apfun

Operating manual for Claude Code in this repository. Terse on purpose. Append to "Lessons learned" whenever the human corrects an approach.

## Source of truth

`project-brief.md` is the authoritative spec. When CLAUDE.md and the brief disagree, the brief wins — update CLAUDE.md to match.

## Directory boundaries (do not violate)

You run inside a dev container. `/workspace` is bind-mounted from the host at `/srv/claude/apfun.online/workspace/`; everything you can see and write lives at or below `/workspace`. The `Dockerfile` and `docker-compose.yml` that govern THIS container live OUTSIDE `/workspace` on the host. Never author a `Dockerfile`, `docker-compose.yml`, or `compose.yml` at the root of `/workspace` to "fix" or replace the dev container — it won't work and the human has to clean up. If the container needs a system package, a new port, or a different CMD, raise it in chat and ask the human to update `/srv/claude/apfun.online/Dockerfile`. See §0 of the brief.

## Networking

Bind any HTTP/ASGI server to `0.0.0.0:4000`. Never `127.0.0.1`. Apache on the host reverse-proxies `https://apfun.online` → host `127.0.0.1:4000` → container `0.0.0.0:4000`. Localhost-only binding inside the container is unreachable from the host. Port 4000 is the only port that matters.

## Model selection policy

All Anthropic calls route through `apfun/llm/client.py` so this policy is enforced in one place. Every call is logged to the `llm_runs` table (model, prompt/completion tokens, latency, est. cost, task).

- **Default: `claude-opus-4-7` with extended thinking at high reasoning effort.** Use for: Stage 1 clustering, Stage 4 scoring, Stage 5 differentiation synthesis, PRD generation, architecture proposals, niche evaluation, competitor analysis, prioritization — anything that needs judgment.
- **Cheap path: `claude-haiku-4-5`.** Use ONLY for trivial mechanical work: dedup ("same problem as that one?"), single-field classification, on-topic filtering, JSON reshape/validation.
- **Never** route a judgment call through Haiku to save tokens. The Max plan absorbs Opus cost; a wrong call in Stage 5 wastes a real human decision, a wrong call in dedup costs cents.
- If a task feels in-between (e.g. summarizing one competitor's reviews), use Opus.
- Use prompt caching on long, repeated context (review corpora, competitor matrices).

## Project conventions

- Python 3.11+. Format with `ruff format`, lint with `ruff check`, type-check with `pyright` (strict on `apfun/`).
- Async-first: FastAPI handlers, `anthropic.AsyncAnthropic`, `httpx.AsyncClient`. Sync code only where a library forces it (e.g. `pytrends`); isolate inside a threadpool.
- Dependencies: `uv`. Add with `uv add`; commit `uv.lock`. Don't hand-edit deps in `pyproject.toml`.
- DB: SQLAlchemy 2.x async + SQLite via `aiosqlite`. Migrations through Alembic, one per logical schema change.
- Tests: `pytest` under `tests/unit/` and `tests/integration/`. Cached SERP/Reddit/HN fixtures under `tests/fixtures/`. No network in unit tests.
- Templates: Jinja2 + HTMX. No JS framework. Minimal Tailwind via the standalone CLI; no Node toolchain.
- Commits: imperative mood ("add reddit ingester"), no co-author line, no emojis. Reference the task number when applicable ("001: scaffold FastAPI app").
- Don't add scope. A bug fix is a bug fix. One task = one PR. If you notice an adjacent issue, log it as a new task rather than expanding the current one.

## File layout

- `apfun/` — application package
- `apfun/main.py` — FastAPI app entrypoint, binds `0.0.0.0:4000`
- `apfun/config.py` — settings (env-driven), feature flags
- `apfun/db.py` — engine + async session factory
- `apfun/models/` — SQLAlchemy ORM models
- `apfun/llm/client.py` — single Anthropic entrypoint; enforces model policy and logs runs
- `apfun/sourcing/` — one module per source (reddit, hn, ph, ih, review_sites)
- `apfun/demand/` — Stage 2 (pytrends, autosuggest)
- `apfun/pipeline/` — Stage 3→4→5 orchestration triggered by HITL approval
- `apfun/scoring/` — Stage 4 saturation scoring
- `apfun/synthesis/` — Stage 5 differentiation synthesis
- `apfun/scheduler/` — APScheduler setup and job registration
- `apfun/web/` — FastAPI routes, Jinja templates, static assets
- `migrations/` — Alembic
- `tests/` — pytest
- `scripts/` — CLI helpers (backfill, replay, dump)
- `docs/tasks/` — sequenced PR-sized task files
- `data/` — SQLite DB (gitignored)

## What not to do

- Don't add Postgres, Redis, Celery, or a JS framework. SQLite + APScheduler + HTMX is v1 and stays v1 until measured pain forces a change. The migration threshold is ~100k `raw_signals` rows.
- Don't add user auth inside the app — Apache basic auth at the edge handles it.
- Don't fire Stage 3+ on raw Stage 1 output. HITL approval is the gate for paid APIs and Opus tokens. Cost discipline is structural, not optional.
- Don't trust LLM summaries of competitor features from SERP snippets — always scrape-then-summarize.
- Don't write a separate production `Dockerfile` in v1 — the dev container is the runtime.
- Don't write CHANGELOG, ADRs, or other meta docs unless asked.

## Lessons learned

(Append corrections from the human here so the lesson persists. Empty to start.)
