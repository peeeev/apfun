# Task list — apfun v1

Sequenced, PR-sized, each ~half-day and independently testable. Numbers are the merge order; dependencies are noted per task. Don't run tasks out of order without checking.

## Phase A — Foundations

- 001 — Project scaffolding (pyproject + uv + ruff/pyright + FastAPI hello on `0.0.0.0:4000`)
- 002 — DB foundations (SQLAlchemy 2 sync + Alembic + `sources`/`raw_signals`/`candidates` with `decision`/`pipeline_stage` split)
- 003 — DB pipeline tables (`demand_checks`, `approvals`, `competitive_analyses`, `scores`, `opportunities`, `projects`, `llm_runs`, `scheduler_runs`)
- 004 — LLM client wrapper (`anthropic.Anthropic` sync, model-policy guard, prompt caching, `llm_runs` logging)

## Phase B — Stage 1 sourcing

- 005 — Reddit ingester (public JSON, per-sub config, content-hash dedup)
- 006 — Hacker News ingester (Algolia "Ask HN" search)
- 007 — ProductHunt ingester
- 008 — IndieHackers ingester
- 009 — Review miner (G2 / Capterra / Trustpilot via Playwright)
- 010a — Signal text normalization (raw_signals → uniform signal_text table; ETL prep for Stage 1)
- 010 — Stage 1 clustering (Opus 4.7: raw signals → candidate idea cards)

## Phase C — Stage 2 demand check + scheduling

- 011 — Stage 2 demand check (pytrends + Google autosuggest, kill-or-survive verdict)
- 012 — Scheduler (APScheduler with SQLite jobstore; register Stage 1 + Stage 2 jobs; idempotent runs)

## Phase D — Admin UI (HITL gate)

- 013 — Admin UI scaffolding (HTMX + Jinja + Tailwind standalone; base layout; navigation)
- 014 — Admin UI inbox (Stage 2 survivors, approve / reject / comment, keyboard shortcuts)

## Phase E — Stages 3–5 (paid + expensive)

- 015 — DataForSEO client + budget guard ($25/mo hard cap, daily usage table)
- 016 — Stage 3 competitor scraping (pricing pages, feature lists, recent funding)
- 017 — Stage 4 saturation scoring (Demand × UnmetPain / IncumbentStrength, full breakdown persisted)
- 018 — Stage 5 differentiation synthesis (Opus 4.7: complaints / feature gaps / pricing gaps / vertical wedge)
- 019 — Pipeline orchestration (HITL approval queues Stage 3 → 4 → 5 onto `BackgroundScheduler`)

## Phase F — Output + remaining UI

- 020 — Admin UI: opportunities list + detail page
- 021 — Admin UI: sources health + projects views
- 022 — Weekly digest email (Mondays 9am; defer provider choice to that task)

## Cross-cutting

- 023 — GitHub Actions CI (sequenced between Phase E and Phase F per orchestrator feedback 009 — PR-review teeth land before the final UI/digest tasks; file-number is post-022 but execution-order is pre-020)
- 005b — Reddit OAuth migration (ABANDONED — Reddit closed self-service OAuth in Nov 2025 under the Responsible Builder Policy; superseded by 005c). Per orchestrator request 020.
- 005c — Reddit residential-proxy + browser-UA migration (post-005b reversal after Reddit API and frontend policy changes; anonymous public-JSON path through `APFUN_REDDIT_HTTP_PROXY` + rotating Chrome UAs). Per orchestrator request 021.
- 024 — `/ops` read-only operator dashboard (KPI cards, scheduler calendar with STALE warnings, source health, LLM cost, recent errors; behind Apache htpasswd). Per orchestrator request 023.
- 025 — Buildability layer: Stage 1's first *evaluation* output (`high`/`medium`/`low`/`non_software` + rationale on `candidates`, via a `cluster.j2` extension + one-time backfill of existing candidates; inbox badges + optional `?hide_non_software` filter). A hint, not a gate; does not feed composite weight. Per orchestrator request 030 (Part 2 of `029-feedback.md`; the request's "task 015" label was a collision with `015-dataforseo-client` — renumbered to 025).
- 014-fix-2 — operator-UX bundle (3 features from triage friction): `/ops` scheduler pause/resume (persisted across restarts via a new `runtime_state` table), inbox nav counts, and candidate merge (`candidates.merged_into_id` soft-delete + Opus N→1 synthesis). Per orchestrator request 031.

## Open questions (from brief §14, parked)

- Email provider for the digest: **Resend** (free tier covers v1 forever). Mailgun / Postmark / SES are fallbacks if verification ever fails.
- DataForSEO monthly cap default ($25 unless contradicted) — wired in task 015.
- Reddit auth (public JSON vs. registered app) — start with public JSON in task 005; revisit if rate-limited.
- SQLite → Postgres migration threshold (~100k `raw_signals`) — track row count, no action in v1.
