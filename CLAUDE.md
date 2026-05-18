# CLAUDE.md — apfun

Operating manual for Claude Code in this repository. Terse on purpose. Append to "Lessons learned" whenever the human corrects an approach.

## Source of truth

`project-brief.md` is the authoritative spec. When CLAUDE.md and the brief disagree, the brief wins — update CLAUDE.md to match.

## Directory boundaries (do not violate)

You run inside a dev container. `/workspace` is bind-mounted from the host at `/srv/claude/apfun.online/workspace/`; everything you can see and write lives at or below `/workspace`. The `Dockerfile` and `docker-compose.yml` that govern THIS container live OUTSIDE `/workspace` on the host. Never author a `Dockerfile`, `docker-compose.yml`, or `compose.yml` at the root of `/workspace` to "fix" or replace the dev container — it won't work and the human has to clean up. If the container needs a system package, a new port, or a different CMD, raise it in chat and ask the human to update `/srv/claude/apfun.online/Dockerfile`. See §0 of the brief.

## Networking

Bind any HTTP/ASGI server to `0.0.0.0:4000`. Never `127.0.0.1`. Apache on the host reverse-proxies `https://apfun.online` → host `127.0.0.1:4000` → container `0.0.0.0:4000`. Localhost-only binding inside the container is unreachable from the host. Port 4000 is the only port that matters.

**Canonical container CMD:** `uv run uvicorn apfun.main:app --host 0.0.0.0 --port 4000`. This is what the host's `docker-compose.yml` should put in its `command:` line. The `python -m apfun.main` form works too (and boots the same app) but the uvicorn CLI is canonical because it exposes `--workers`, `--log-level`, etc. without code changes.

## Model selection policy

All Anthropic calls route through `apfun/llm/client.py` so this policy is enforced in one place. Every call is logged to the `llm_runs` table (model, prompt/completion tokens, latency, est. cost, task).

- **Default: `claude-opus-4-7` with extended thinking at high reasoning effort.** Use for: Stage 1 clustering, Stage 4 scoring, Stage 5 differentiation synthesis, PRD generation, architecture proposals, niche evaluation, competitor analysis, prioritization — anything that needs judgment.
- **Cheap path: `claude-haiku-4-5`.** Use ONLY for trivial mechanical work: dedup ("same problem as that one?"), single-field classification, on-topic filtering, JSON reshape/validation.
- **Never** route a judgment call through Haiku to save tokens. The Max plan absorbs Opus cost; a wrong call in Stage 5 wastes a real human decision, a wrong call in dedup costs cents.
- If a task feels in-between (e.g. summarizing one competitor's reviews), use Opus.
- Use prompt caching on long, repeated context (review corpora, competitor matrices).

## Orchestrator Pattern (External Review)

This project uses a two-tier review process. You (Claude Code, in this container) are the **Implementer** — you write code, run tests, open PRs. A separate Claude session outside the container, accessed via the human, is the **Orchestrator** — it reviews architecture, enforces gates, catches drift across tasks.

You don't talk to the Orchestrator directly. The human is the bridge: you write structured request files; the human pastes them into the chat; you receive responses as saved feedback files. This codifies a paper trail of architect-level decisions inside the repo.

### When to initiate an Orchestrator request

Proactively, without being asked:

- **Phase transitions** in `docs/tasks/000-overview.md` (finishing Phase A foundations before starting Phase B sourcing, etc.).
- **Open questions** you can't resolve from `project-brief.md`, `CLAUDE.md`, or prior feedback. Don't guess; ask.
- **Schema migrations spanning >2 tables.** Foundational changes get reviewed.
- **Tech-stack deviations** from `project-brief.md` §5.
- **Anything that would materially change `CLAUDE.md` or `project-brief.md`.**

Also: whenever the human says "run this by the Orchestrator."

### Don't escalate for these (just keep going)

- Per-task code review (the human reviews PRs inline).
- Bug fixes that don't change architecture.
- Tests, refactors within a single module, docstring improvements.
- New endpoints that fit existing patterns.

The Orchestrator is for direction-checking, not granular review. Over-escalation defeats the point.

### How to package a request

Write a file to `docs/orchestrator/NNN-short-topic.md` where NNN is zero-padded, one higher than the latest in that directory.

Template:

```markdown
# Request NNN: <topic>

**Context** (1-3 sentences): where we are in tasks, what just happened.

**What I just did** (or am about to do): summarize concretely.

**What I would do next without intervention**: the path I'd take silently.

**Specific questions or risks**: bulleted; what I want flagged.

**Relevant files/diffs**: paste inline or list paths. Keep it tight — verbose summaries lose the reader.
```

Then update `docs/orchestrator/INDEX.md` with a new row:

```
| NNN | YYYY-MM-DD | short-topic | open | --- |
```

Tell the human: "Orchestrator request NNN is ready in `docs/orchestrator/`." Stop and wait — don't proceed past the gate without feedback.

### How to consume feedback

At the start of every session, before any work:

1. `ls docs/orchestrator/*-feedback.md`
2. Read every feedback file you haven't acknowledged yet (oldest first).
3. Update `INDEX.md`: change the row's status from `open` to `answered`, add a one-line summary of the decision in the last column.
4. Append any durable lesson to `CLAUDE.md`'s "Lessons Learned" section.

### Authority hierarchy

If sources conflict, the precedence is:

1. **Latest Orchestrator feedback** (highest authority — most recent direction wins)
2. `project-brief.md` and `CLAUDE.md` (update these if feedback supersedes them)
3. Earlier feedback files
4. Your own prior decisions

If feedback contradicts an earlier decision, the feedback wins. If feedback contradicts `CLAUDE.md` or `project-brief.md`, update the doc in the same PR and note the change.

### INDEX.md format

Append-only, one line per request:

```
| NNN | YYYY-MM-DD | topic-slug                | status   | one-line decision summary       |
|-----|------------|---------------------------|----------|---------------------------------|
| 001 | 2026-05-20 | gate2-stack-confirmation  | answered | sync DB, split status, Resend   |
| 002 | 2026-05-24 | phase-a-complete          | open     | ---                             |
```

Status values: `open` (request submitted, no feedback yet), `answered` (feedback received and applied).

## Project conventions

- Python 3.11+. Format with `ruff format`, lint with `ruff check`, type-check with `pyright` (strict on `apfun/`).
- **Concurrency model: sync everywhere except FastAPI handlers.** Handlers may be `async def` (framework convention) but only do short, non-blocking DB reads and quick task enqueues — no LLM calls or scrapes inline. Long work runs on APScheduler `BackgroundScheduler` jobs (sync threads). LLM client: `anthropic.Anthropic` (sync). HTTP scrapers: `httpx.Client` (sync). **Why:** SQLite + async + concurrent writes from APScheduler is a known locking footgun; sync threading + `busy_timeout` + WAL gives clear serializable-write semantics.
- DB: SQLAlchemy 2.x sync + SQLite via stdlib `sqlite3`. Apply pragmas via a `connect` event listener so they fire on every new connection (the pool can open new ones at any time): `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA busy_timeout=5000; PRAGMA foreign_keys=ON`. Migrations through Alembic, one per logical schema change.
- Dependencies: `uv`. Add with `uv add`; commit `uv.lock`. Don't hand-edit deps in `pyproject.toml`.
- Tests: `pytest` under `tests/unit/` and `tests/integration/`. Cached SERP/Reddit/HN fixtures under `tests/fixtures/`. No network in unit tests.
- Templates: Jinja2 + HTMX. No JS framework. Minimal Tailwind via the standalone CLI; no Node toolchain.
- Commits: imperative mood ("add reddit ingester"), no co-author line, no emojis. Reference the task number when applicable ("001: scaffold FastAPI app").
- Task files in `docs/tasks/` carry a `**Complexity:** S/M/L` line (S ≈ 1h, M ≈ half-day, L ≈ full day) for planning.
- Don't add scope. A bug fix is a bug fix. One task = one PR. If you notice an adjacent issue, log it as a new task rather than expanding the current one.

## File layout

- `apfun/` — application package
- `apfun/main.py` — FastAPI app entrypoint, binds `0.0.0.0:4000`
- `apfun/config.py` — settings (env-driven), feature flags
- `apfun/db.py` — engine + sync session factory (WAL pragmas applied on connect)
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

- 2026-05-17: DB layer is sync (`sqlite3` + sync SQLAlchemy + `BackgroundScheduler`). SQLite + async + concurrent writes is a known locking footgun. See "Concurrency model" in Project conventions.
- 2026-05-17: `candidates` has two status columns: `decision` (HITL outcome — pending/approved/rejected/auto_killed) and `pipeline_stage` (machine progress — none/competitive/scoring/synthesizing/done/failed). Don't merge.
- 2026-05-17: Weekly digest provider defaults to Resend (free tier covers v1 forever).
- 2026-05-17: Each task file in `docs/tasks/` carries a `**Complexity:** S/M/L` line.
