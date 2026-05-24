# CLAUDE.md — apfun

Operating manual for Claude Code in this repository. Terse on purpose. Append to "Lessons learned" whenever the human corrects an approach.

## Source of truth

`project-brief.md` is the authoritative spec. When CLAUDE.md and the brief disagree, the brief wins — update CLAUDE.md to match.

## Directory boundaries (do not violate)

You run inside a dev container. `/workspace` is bind-mounted from the host at `/srv/claude/apfun.online/workspace/`; everything you can see and write lives at or below `/workspace`. The `Dockerfile` and `docker-compose.yml` that govern THIS container live OUTSIDE `/workspace` on the host. Never author a `Dockerfile`, `docker-compose.yml`, or `compose.yml` at the root of `/workspace` to "fix" or replace the dev container — it won't work and the human has to clean up. If the container needs a system package, a new port, or a different CMD, raise it in chat and ask the human to update `/srv/claude/apfun.online/Dockerfile`. See §0 of the brief.

## Networking

Bind any HTTP/ASGI server to `0.0.0.0:4000`. Never `127.0.0.1`. Apache on the host reverse-proxies `https://apfun.online` → host `127.0.0.1:4000` → container `0.0.0.0:4000`. Localhost-only binding inside the container is unreachable from the host. Port 4000 is the only port that matters.

**Canonical container CMD:** `uv run uvicorn apfun.main:app --host 0.0.0.0 --port 4000`. This is what the host's `docker-compose.yml` should put in its `command:` line. The `python -m apfun.main` form works too (and boots the same app) but the uvicorn CLI is canonical because it exposes `--workers`, `--log-level`, etc. without code changes.

**Reddit access (task 005c).** Reddit ingestion has *two independent gating layers* and requires both to be addressed:

1. **Network.** Datacenter IPs are blocked at the network layer — requires a residential proxy via `APFUN_REDDIT_HTTP_PROXY` (URL format `http://user:pass@host:port`; the env var accepts a single proxy URL — providers that assign one IP per port mean the operator picks one port). Loud-failure at the `_build_client()` call site if the var is empty and Reddit sources run.
2. **Application.** The web frontend (`www.reddit.com`) filters non-browser UAs since June 2025 — including the PRAW-style self-identifying UA that was appropriate for *authenticated* API access. The anonymous public-JSON path is treated as browser traffic, so we send a rotating pool of recent Chrome UAs (`USER_AGENT_POOL`) plus a full browser header set (`BROWSER_HEADERS`), not a self-identifying UA.

Both are implemented in `apfun/sourcing/reddit.py`. Operator setup (pick a residential-proxy provider, set the env var, restart) lives in `docs/operator/SETUP.md` → Reddit access. This supersedes task 005b (OAuth), abandoned when Reddit closed self-service OAuth credential creation in November 2025. If proxy + browser-UA still gets blocked, the next escalation is a JS-capable client (Playwright) — task 005d, not pre-built.

## Dev runtime workflow

**`/workspace` is the deployment surface, not the development surface.** The container runs uvicorn with `--reload --reload-dir /workspace/apfun`, so any change to `/workspace/apfun/*.py` reloads the live service, and any branch checkout there transiently runs unmerged code against production.

1. **`/workspace` stays permanently on `main`.** No branch checkouts there; no editing files there mid-task. (If a `git pull` ever "does nothing," first check `git branch --show-current` — the shared checkout may have drifted off main.)
2. **All branch work happens in git worktrees outside the watched path** — `git worktree add /tmp/apfun-<task> -b <branch> main`. The worktree gets its own `uv` venv (fast from the global cache), so ruff/pyright/pytest run against the *branch's* code without touching `/workspace`. Worktrees are local + independent of the remote branch (`git worktree remove` works even after the GitHub branch is deleted; `git worktree prune` if a `/tmp` worktree was wiped by a container restart).
3. **Deploy = `git pull` on `main`** inside `/workspace`; `--reload` picks up the new code automatically. No container restart needed for code.
4. **Schema migrations run in `/workspace`, on `main`, post-pull** — they touch the live `data/apfun.db`, which lives there (not in worktrees). Snapshot first (`make migrate` does this).
5. **Orchestrator request/feedback files** are read-and-pasted artifacts: after building one in a worktree, also drop the standalone `.md` at `/workspace/data/orchestrator-requests/<name>.md` (shared with the host, gitignored under `data/`) so the operator can open it — `/tmp` is container-local and NOT visible to the host shell.

(Dropping `--reload` for deliberate restart-on-deploy was considered and rejected — the credential re-login cost per restart was why `--reload` was enabled; the worktree discipline is lighter than recurring restart pain. Per orchestrator feedback 029 Q3.)

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

## Git workflow

**Branch per task.** Direct commits to `main` are reserved for repo-wide infra (initial scaffold, hotfixes the human explicitly authorizes). Everything else lands via PR.

- Branch name: `feature/task-NNN-short-slug` (matches `docs/tasks/NNN-*.md`). For non-task work (a feedback fold that doesn't map to a numbered task), use `feature/feedback-NNN-applied` or `feature/<descriptive-slug>`.
- Orchestrator request and feedback files commit on the same branch as the work they belong to — the PR becomes the canonical place for "what did this task decide?" archaeology.
- Open the PR when the task is ready for review. The human merges. Task 023's CI (when live) runs on PRs.
- Don't push to `main` directly even if access allows it. The branch+PR cadence is the rule; classifier blocks are the guardrail enforcing it.

## Project conventions

- Python 3.11+. Format with `ruff format`, lint with `ruff check`, type-check with `pyright` (strict on `apfun/`).
- **Concurrency model: sync everywhere except FastAPI handlers.** Handlers may be `async def` (framework convention) but only do short, non-blocking DB reads and quick task enqueues — no LLM calls or scrapes inline. Long work runs on APScheduler `BackgroundScheduler` jobs (sync threads). LLM client: `anthropic.Anthropic` (sync). HTTP scrapers: `httpx.Client` (sync). **Why:** SQLite + async + concurrent writes from APScheduler is a known locking footgun; sync threading + `busy_timeout` + WAL gives clear serializable-write semantics.
- DB: SQLAlchemy 2.x sync + SQLite via stdlib `sqlite3`. Apply pragmas via a `connect` event listener so they fire on every new connection (the pool can open new ones at any time): `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA busy_timeout=5000; PRAGMA foreign_keys=ON`. Migrations through Alembic, one per logical schema change.
- **JSON columns are reassign-only.** SQLAlchemy does not track in-place mutations of dict/list values in `JSON` columns. Build the new value locally and assign it whole: `row.payload_json = {**row.payload_json, "k": "v"}`, never `row.payload_json["k"] = "v"`. Use `MutableDict.as_mutable(JSON)` only for a specific column that genuinely needs in-place tracking — most of ours are write-once.
- **Every FK column gets an explicit index.** SQLite does not auto-index FK columns. Add `index=True` on the `mapped_column`. A UNIQUE constraint already implies an index, so FK+UNIQUE columns don't need another one.
- **Verify external constants inline.** A constant is "external" if its value originated from a third-party document, API specification, pricing page, or other source outside this repository. External constants carry one of two annotations immediately above the line(s) they apply to:
  - `# verified YYYY-MM-DD <source-url-or-doc-path>` — when an authoritative source exists (pricing pages, API docs, RFCs, published rate limits).
  - `# heuristic YYYY-MM-DD — <rationale and any reference>` — when the value is judgment-derived from incomplete information (community consensus, observed-good-behavior thresholds, defensive headroom). Different keyword from `verified` so `grep -r '# heuristic'` audits judgment-derived values across the codebase.

  Internal project decisions (`host="0.0.0.0"`, enum values, computed bounds, fallback defaults) don't require annotation — their authority is the codebase. When in doubt, ask: *"if a teammate questioned this number, would I cite an external URL or a project decision?"* External constants without one of the two annotations are treated as unverified during code review; when you touch one, verify or annotate as part of that task — don't defer. The convention IS the audit; no standalone audit scripts (they decay).

  **TODO verify resolution.** When a `# verified` URL can't be sourced in-PR (third-party docs page moved, login-walled, broken), use `# TODO verify by end of task <NNN>: <reason>` instead, citing whichever fallback source the value came from. Grep for `# TODO verify` at task end — result must be zero, or the unresolved items get escalated to the orchestrator before the task closes. Task-scoped TODOs force resolution within the natural work boundary instead of accumulating.
- **Contract tests for external schemas.** When parsing third-party API responses, assert the fields your code depends on in a `tests/unit/test_<source>_schema_contract.py` test against a captured fixture in `tests/fixtures/<source>/`. If the test fails after a fixture refresh, the third party changed their response shape — investigate before adjusting the parser. Sibling rule to verify-constants: VALUES get annotations, SCHEMA SHAPES get tests. SDK-shipped models (e.g., `anthropic.types.Message`) get tripwire tests via `Message.model_validate` against a fixture (see `tests/unit/test_anthropic_response_shape.py`) — qualitatively different because they validate against a model the SDK ships, not against your own parsing assumptions.
- **HITL decisions are durable.** New evidence prompts re-review but never auto-flips a decision. The operator rejected (or approved) for a reason; only an explicit re-decision changes the status. Stage 1 clustering linking new signals to an already-rejected candidate is *fine* — it lets the inbox surface "N new signals since rejection — re-review?" — but the `decision` itself stays whatever the operator last set. Per orchestrator feedback 016 Q5.
- **Operator runbooks.** Short numbered procedures for "execute and capture" sessions live in `docs/operator/runbooks/NNN-topic.md`. Each runbook lists commands, expected outputs, and what artifacts to bring back to the orchestrator. Used when a design decision requires empirical input rather than first-principles reasoning. Sibling to `docs/orchestrator/` in the project's decision history: orchestrator files are *reasoning we did*; runbook files are *things we ran*. Per orchestrator feedback 017.
- **Every pipeline stage requires a runbook before scheduler integration.** Each of Stages 1-5 gets a `docs/operator/runbooks/NNN-<stage>-first-pass.md` and an actual empirical-input session before the scheduler is wired to run it on a cadence. Synthetic tests are necessary but not sufficient — they miss transaction-shape bugs, LLM-quirk bugs, and upstream-API-change bugs (see Lesson Learned 2026-05-22). The 30-60 minutes of operator time is cheap insurance against silent production data loss. Per orchestrator feedback 018.
- **Docs update with the code.** When a task changes external interfaces, env vars, conventions, or operator procedures, the relevant docs (CLAUDE.md, README, `.env.example`, operator runbooks under `docs/operator/`, task specs under `docs/tasks/`) are updated in the same PR — not as a follow-up. Documentation drift is the silent killer of long-running projects; the cost of writing docs when the change is fresh is much lower than reconstructing context weeks later. Sibling rule to verify-constants and contract-tests: verify-constants protects VALUES, contract-tests protect SHAPES, docs-update protects MEANINGS. Per orchestrator request 020.
- **Migration data-preservation discipline.** Every Alembic migration touching a table with `ON DELETE CASCADE` or `ON DELETE SET NULL` children (or any table whose recreation can trigger them via `render_as_batch`) must be **data-preservation-tested**, not just schema-tested. The test seeds parent + N child rows, applies the migration, and asserts: (1) parent row count unchanged, (2) child row count unchanged, (3) foreign keys still resolve, (4) cascading-nullable columns (e.g., `llm_runs.candidate_id`) preserved. An empty-DB validation is insufficient — the failure mode requires real child rows to surface. Tests live in `tests/integration/test_migration_fk_safety.py` (or a sibling per migration) and run in `make test-all`. If a migration's diff includes `op.batch_alter_table(...)` against a table referenced by any FK, the PR description must explicitly confirm "data-preservation tested" before merge. (`migrations/env.py` also runs migrations with `foreign_keys=OFF` so batch recreates can't cascade-wipe — belt to this suspenders.) Per orchestrator feedback 029 Q1; tuition was the 7f3a9c2e1d04 cascade wipe.
- **Pre-migration snapshots.** Before any `alembic upgrade` against `data/apfun.db`, snapshot via `scripts/db_snapshot.sh` (or `make migrate`, which snapshots first). It copies the live DB to `data/backups/apfun-<rev>-<timestamp>.db` (gitignored) using SQLite's online-backup API, keeping the most recent 10. **Why it matters:** layers split into *durable* (raw_signals, signal_text — source of truth from external systems, only re-acquirable by re-scraping) and *reconstructible-or-lossy* (candidates, candidate_signals, approvals, llm_runs — derived or operator-time). Re-running pipelines rebuilds the derived layers; but operator-time artifacts (approval decisions + notes) are NOT derivable and demand real backups. "Recoverable in principle" is worthless if no backup exists at the moment data is needed. Per orchestrator feedback 029 Q2.
- **Auth secret discipline.** External-service secrets are env vars under the `APFUN_` prefix. The fail-loud point depends on *how the third party fails when the secret is missing*:
  - **Silent degradation** (returns wrong-account results, phantom-empty data, or otherwise plausible-looking-but-wrong output) → fail at `Settings()` construction with a CLAUDE.md-pointing message. *Example:* `APFUN_REDDIT_USERNAME` — Reddit silently degrades non-conformant UAs, so missing username produces phantom-empty results that look like "no new content."
  - **Loud failure** (returns a clear authentication error like 401/403 with a meaningful message) → empty default; fail at the call site with a clear message. *Examples:* `APFUN_ANTHROPIC_API_KEY`, `APFUN_PRODUCTHUNT_TOKEN`.

  When in doubt about which category a service falls into, test it: configure the service intentionally wrong and observe whether it errors or silently returns garbage. The empirical answer governs.
- Dependencies: `uv`. Add with `uv add`; commit `uv.lock`. Don't hand-edit deps in `pyproject.toml`.
- Tests: `pytest` under `tests/unit/` and `tests/integration/`. Cached SERP/Reddit/HN fixtures under `tests/fixtures/`. No network in unit tests. `make test` runs unit only; `make test-all` includes the live-API integration suite (~$0.05/run total for the LLM smoke tests) — use it intentionally.
- **Synthetic-fixture forcing function.** If `make check` fails only on `tests/unit/test_anthropic_response_shape.py::test_fixture_is_real_capture`, that's the intentional prompt to replace the synthetic Opus 4.7 response fixture with a real capture. Resolve by running `APFUN_ANTHROPIC_API_KEY=... uv run python scripts/capture_response_fixture.py` and committing the regenerated `tests/fixtures/opus_4_7_with_cache.json`. Until then, `make check` red on this single test is expected and doesn't block other work — run subset gates (`pytest tests/unit/test_<area>*.py`) to verify task-specific status.
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
- `apfun/llm/client.py` — single Anthropic entrypoint; enforces model policy and logs runs. `judge()`/`mechanic()` return `Message`; `judge_json()`/`mechanic_json()` validate against a Pydantic schema and retry on `JSONParseError` inside the same retry budget as transient API errors.
- `apfun/llm/prompts/` — Jinja templates for LLM stages (`cluster.j2`, `cluster_merge.j2`, etc.). Strict-JSON instructions live here; the prompts are the load-bearing artifact for stage quality.
- `apfun/sourcing/` — one module per source (reddit, hn, ph, ih, review_sites)
- `apfun/demand/` — Stage 2 (pytrends, autosuggest)
- `apfun/pipeline/` — Stage 0 normalization (`normalize.py` + `_extractors.py`, raw_signals → signal_text) and Stage 3→4→5 orchestration triggered by HITL approval
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
- 2026-05-18: JSON columns are reassign-only (no MutableDict by default). See "JSON columns are reassign-only" in Project conventions.
- 2026-05-18: Every FK column gets an explicit `index=True`; SQLite doesn't auto-index FKs.
- 2026-05-18: SQLAlchemy `Enum(native_enum=False)` doesn't emit a DB-level `CHECK` constraint by itself — add `CheckConstraint(check_enum_sql(...))` in `__table_args__`. Helpers live in `apfun/models/base.py`.
- 2026-05-18: When the brief says "do X, not Y," encode it as a runtime guardrail with a helpful error pointing back at CLAUDE.md, not just prose. Example: the host validator in `apfun/config.py` rejects `127.0.0.1` with an error citing §Networking. CLAUDE.md is for humans; the validator is for future Claude Code sessions and tired-day humans both.
- 2026-05-18: Within a single PR, separate plan/doc changes from code changes into distinct commits. Keeps `git log` readable when reviewing what shifted in plan vs what landed in code (see `503689e` → `2f76b51` for the pattern).
- 2026-05-18: Wrap paid external services (LLM, DataForSEO, future scraping APIs) behind a policy-compliant facade — single import path, policy enforcement and audit logging centralized inside the wrapper, not at call sites. Ship the per-call audit table + a daily/monthly aggregate in the first task that touches the service. Retrofitting later means weeks of untracked spend.
- 2026-05-18: Pricing tables for external APIs are module-level dicts with a `# verified YYYY-MM-DD` comment. Compute the dollar value at call time and persist the dollar value (not the formula) to the audit row. Historical rows survive future price changes; the comment dates the assumptions.
- 2026-05-18: Be explicit about `max_retries` and per-call timeouts on every external SDK; don't rely on defaults. Different call kinds get different timeouts (`judge` 120s vs `mechanic` 30s in `apfun/llm/client.py`). Logging the attempt count on the audit row makes retry storms visible after the fact.
- 2026-05-18: When the brief specifies an optional API feature (extended thinking, prompt caching, tool use, etc.), verify with a test that the actual request payload carries the parameter — not just that the wrapper accepts a kwarg. Otherwise the wrapper looks correct but silently degrades to defaults.
- 2026-05-18: Tables holding LLM/judgment output (`scores`, `opportunities`, future synthesized records) carry a `model_version` (or formula-revision) column so the underlying formula or model can evolve without losing historical interpretability.
- 2026-05-18: Memory is unreliable for fast-moving external data (pricing, model IDs, API parameters). When the brief or feedback says "verify against the published source," actually fetch it — the first `PRICING` table in `apfun/llm/client.py` was 3× off for Opus 4.7 because the values were filled in from memory rather than checked. Module-level `# verified YYYY-MM-DD` comments are only as good as the verification.
- 2026-05-18: For per-task LLM defaults (thinking budget, max tokens, model selection nuances), use a module-level dict keyed by task name, not a single flat default. Stage 5 synthesis deserves more reasoning budget than Stage 1 dedup; spending the policy at one number wastes the higher tier and under-thinks the important calls. See `DEFAULT_THINKING_BUDGET` in `apfun/llm/client.py`.
- 2026-05-18: `llm_runs.retry_log_json` captures per-attempt error details for retries BEFORE the final attempt; the final attempt's outcome lives in the top-level `ok`/`error`/`latency_ms` columns. Retries above 1 without a populated `retry_log_json` is a bug.
- 2026-05-18: External constants require either `# verified YYYY-MM-DD <source>` (authoritative source exists) or `# heuristic YYYY-MM-DD — <rationale>` (judgment-derived from incomplete info) annotation inline; see "Verify external constants inline" in Project conventions. Distinct keywords are deliberate: `grep -r '# heuristic'` audits judgment-derived values. No standalone `verify_*.py` scripts.
- 2026-05-18: External integrations have three distinct protection mechanisms by what changes. VALUES (rate limits, prices, model IDs) → `# verified`/`# heuristic` annotations. SCHEMA SHAPES from third-party APIs (Reddit, ProductHunt GraphQL, G2/Capterra HTML) → contract tests at `tests/unit/test_<source>_schema_contract.py` against captured fixtures. SDK-SHIPPED MODELS (`anthropic.types.Message`) → tripwire tests with `model_validate` against captured response. Pick the mechanism by what changes.
- 2026-05-18: `DEFAULT_THINKING_BUDGET` retune triggers — first of: 50 rows in `llm_runs` for any single task, a `judge()` call hitting its budget warning (>90% of budget used), or 10 `synthesize` calls. When any fires, open an orchestrator request with `llm_runs` aggregates — don't tune silently.
- 2026-05-18: `JUDGMENT_TASKS` membership is semantically anchored to `project-brief.md` §3, not to "things I added LLM calls for so far." Anything involving niche evaluation, competitor analysis, prioritization, or "is this opportunity real" belongs in the set as it materializes.
- 2026-05-19: Branch per task (`feature/task-NNN-...`). Don't push to `main` even if access allows it — PRs give changes a moment to be visible before permanent, are the natural place to attach orchestrator request/feedback files, and play nicely with the task 023 CI gate. The classifier block on direct-to-main pushes is the guardrail enforcing this; see "Git workflow" in this file.
- 2026-05-19: Auth-secret fail-loud point is determined by the third party's failure mode, not by personal preference. Silent-degradation services (Reddit) fail at `Settings()` construction; loud-failure services (Anthropic, ProductHunt) fail at the call site with empty default. See "Auth secret discipline" in Project conventions. When adding a new secret, test the empirical failure-on-missing-secret behavior before deciding the fail-loud point.
- 2026-05-19: Don't bundle refactoring with feature work. When a third call site reveals the right abstraction shape across N similar modules, the unification PR is its own commit/PR with no behavior change — title pattern `refactor: extract shared sourcing skeleton (no behavior change)`. Keeps the diff reviewable on its own merits, separates "did the refactor break anything?" from "is the new feature correct?".
- 2026-05-21: HITL decisions are durable; new evidence prompts re-review but never auto-flips a decision. See "HITL decisions are durable" in Project conventions. Stage 1 clustering links new signals to already-rejected candidates without changing their `decision`; the inbox UI computes `signals_since_rejection` from `candidate_signals.created_at > approvals.decided_at` so re-review prompts surface deliberately rather than as side-effects.
- 2026-05-21: When the LLM wrapper needs JSON validation, prefer `judge_json()`/`mechanic_json()` (pass a Pydantic schema) over post-hoc parsing at the call site. The wrapper integrates `JSONParseError` into its retry loop alongside transient API errors — one retry budget, one log artifact (truncated raw response in `llm_runs.error` on final failure). Per feedback 016 Q3.
- 2026-05-22: **Synthetic tests don't catch surface-changing bugs.** Three categories of production bugs survived weeks of synthetic-test coverage and were caught in the first hour of runbook 001 (2026-05-22):
  1. **Transaction-shape bugs** where the test's commit cadence diverged from production's (e.g., SAVEPOINT scope inside a multi-statement batch — PR #10).
  2. **LLM-quirk bugs** where the stub doesn't reproduce real-model formatting quirks (e.g., markdown fences around JSON output — PR #11).
  3. **Upstream-API-change bugs** where the SDK's deprecations aren't covered by mocked responses (e.g., Opus 4.7's `thinking.type="enabled"` → `"adaptive"` migration — PR #11).

  For Stage 1+ work, plan a runbook-shaped empirical session shortly after writing tests. Cost is cheap (30-60 minutes); the alternative is silent production data loss or scheduler crashes. See also "Every pipeline stage requires a runbook before scheduler integration" in Project conventions. Per orchestrator feedback 018 Q5.
- 2026-05-22: **External-API policy changes can invalidate engineering effort mid-project, and one symptom can mask multiple causes.** Reddit's November 2025 Responsible Builder Policy made the OAuth migration from task 005b obsolete before it ever ran (self-service OAuth credential creation was closed). Investigating the workaround surfaced a *second* independent block: Reddit's web frontend started filtering by User-Agent in June 2025, meaning the PRAW-style UA convention (correct for authenticated API access) is wrong for the anonymous JSON path. The 403s seen from runbook 001 onward had two causes, not one. Single-cause diagnostic mental models miss layered defenses; verify each layer independently when a workaround unblocks the first symptom. Discipline: web-search the current state of any third-party API's access policy *and* anti-bot posture before drafting a migration spec, regardless of how confident the prior model is. Per orchestrator request 021. (Task 005b → 005c reversal was the tuition.) The workaround (residential proxy + browser-mimicking UA pool + full browser header set) succeeded against both layers as of 2026-05-22; runbook 003 closed green.
- 2026-05-22: **Fixtures serve two different jobs; one file can't do both.** Value-asserting tests need controlled synthetic data (deterministic; failures indicate parser bugs). Schema-contract tests need captured real data (variable; failures indicate upstream shape drift). When a runbook instruction says "refresh the fixture from real data," check whether the existing fixture is load-bearing for value assertions before overwriting. Safer pattern: two fixtures, one synthetic + one real, each pointed at by the tests that want that flavor (Reddit: `listing_saas.json` synthetic for `test_reddit_ingester.py`, `listing_saas_real.json` real for `test_reddit_schema_contract.py`). Apply to other ingesters as their fixture sets evolve. Per orchestrator feedback 022 Q1.
- 2026-05-23: **Real-world signal text isn't uniformly complaint-shaped — schemas should be able to express "no judgment applicable".** Stage 1's Haiku dedup pass returned `core_complaint: null` for signals that didn't contain a complaint (deleted Reddit posts, Show-HN announcements, off-topic content), tripping the strict-string `SignalCoreComplaint` schema and aborting the whole cluster run via the JSONParseError retry loop. The fix accepts null as a valid Haiku output and marks those signals `signal_text.is_low_signal=True` so they don't get re-Haiku'd on future runs. Generalizable: when integrating an LLM-judgment step, prefer schemas that can express "no judgment applicable" (Optional fields, sentinel values) over schemas that force the model to invent answers it doesn't have — the invented answers pollute downstream pipelines. Per orchestrator request 024.
- 2026-05-23: **Don't name template-context dict keys after dict methods.** Jinja2 resolves `{{ r.items }}` as `getattr(r, 'items')` first and only falls back to `r['items']` — so a dict key named `items` renders as the bound `.items()` method (`<built-in method items of dict object at 0x…>`), not the value. Same trap for `keys`, `values`, `update`, `get`, `pop`, `copy`. When building template context dicts, use specific names (e.g., `items_processed`) and avoid the dict-method namespace. The `r['items']` subscript syntax forces the dict-key lookup if a name collision is unavoidable, but renaming is cleaner. Tuition was the /ops Recent-runs cell rendering as the method repr (PR #21 fix).
- 2026-05-24: **Verify referenced affordances before assuming they exist.** The orchestrator describes desired/expected shapes that may not match implemented shapes. When an instruction says "click here," "look at this field," or otherwise references UI/CLI affordances — or module paths, env-var names, file paths, DB columns — that haven't been verified against the current codebase, sanity-check first (browser, grep, "actually I don't see that" pushback) rather than assuming the mental model matches. The orchestrator commits to verifying before describing; the implementer/operator commits to checking before trusting. (Example caught this turn: request 028 specced a new `approvals.notes` column, but `approvals.comment` already existed and serves the same purpose — reused it instead of adding a redundant column.) Per orchestrator request 028.
- 2026-05-24: **SQLite batch migrations cascade-delete child rows if foreign keys are enforced.** `render_as_batch` migrations recreate the table (CREATE new → copy → DROP old → rename); with `PRAGMA foreign_keys=ON`, SQLite's implicit DELETE-before-DROP fires every `ON DELETE CASCADE` pointing at the table and silently wipes child rows. Migration `7f3a9c2e1d04` (a CHECK-constraint rewrite on `candidates`) destroyed all `candidate_signals` + `approvals` rows on the live DB this way (no backup; only the decisions on `candidates` itself survived). Fix: `migrations/env.py` runs migrations through a dedicated engine that sets `PRAGMA foreign_keys=OFF` at connect time — NOT via `connection.execute` after connect (SQLite ignores the pragma mid-transaction, and it breaks Alembic's commit handling). **Validation discipline: a migration test that runs on an EMPTY DB proves nothing about data preservation — seed parent + child rows first, run the migration, then assert the children survive** (see `tests/integration/test_migration_fk_safety.py`). The "validated on a throwaway DB" claim that let this through tested the constraint change, not the cascade. Sibling to the contract-test/verify-constants conventions: migrations that touch a table with cascade children must be data-preservation-tested, not just schema-tested.
