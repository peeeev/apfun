# Request 019: next direction after 013+014 merge

**Context.** PR #13 merged (013 admin UI base + 014 inbox endpoint, bundled per feedback 018 Q1). The inbox is live at `https://apfun.online/inbox` rendering the 11 reviewable candidates from runbook 001. Feedback 018 cleared the empirical-input gate for Stage 1; Stage 0 normalization + Stage 1 clustering are both shipped and validated end-to-end against real HN data. Three deferred items from 018 are tracked (cost re-validation, cache_blocks wiring, Reddit Q4) but are explicitly N=100+ post-scheduler concerns.

**What I just did.**

- `apply feedback 018: routing 013+014 + convention updates` — INDEX row 018 → answered, save the feedback file, add CLAUDE.md convention "Every pipeline stage requires a runbook before scheduler integration", add Lesson Learned 2026-05-22 codifying the three-category framing of bugs synthetic tests miss (transaction-shape / LLM-quirk / upstream-API-change), task 010 Notes section listing three N=100+ re-validation gates.
- `018 Q3: add effort column to llm_runs` — Alembic migration + ORM column + wrapper persistence on all four log paths (`_log_success` final, `_log_failure` retryable branch, `_log_failure` APIError branch, `_log_failure` JSON parse-failure branch).
- `013+014: admin UI base + inbox endpoint` — `apfun/web/` package with `routes/`, `templates/`, `static/`; HTMX 2.0.4 pinned + SRI; dark-mode default; `_base.html` chrome with nav; `/healthz` moved into web router; `/static` mounted; root meta-redirects to `/inbox`; `app.css` hand-curated subset; `scripts/build_css.sh` documents future Tailwind-standalone-CLI path. Inbox endpoint lists pending candidates ordered by composite signal weight desc + rejected-with-new-signals in a "Re-review?" section; `signals_since_rejection` computed via `candidate_signals.created_at > approvals.decided_at`; HITL-durable (decision stays whatever operator set). 13 unit tests covering empty state, ordering, mutations, 404, signals_since_rejection surface, and HITL durability.

Gate: ruff format/lint + pyright clean; 214/215 unit tests pass (only the expected synthetic-fixture sentinel fails per CLAUDE.md forcing function). Browser smoke confirmed `/`, `/inbox`, `/healthz`, `/static/app.css` all serve.

**What I would do next without intervention.**

**Task 012 (scheduler).** APScheduler `BackgroundScheduler` inside FastAPI lifespan, `SQLAlchemyJobStore` so restarts don't lose jobs. Register the four ingest jobs we have (Reddit, HN, ProductHunt, IndieHackers, review miner — task 009 ships review-miner already), Stage 0 normalization, Stage 1 clustering. Stop short of Stage 2 demand check until task 011 ships. Every job wrapped to write a `scheduler_runs` row; `coalesce=True` + `max_instances=1` to prevent overlap; idempotent re-runs.

Rationale: runbook 001 already cleared the empirical-input gate for Stage 1 (the new "every pipeline stage requires a runbook before scheduler integration" convention is satisfied). Stage 0 has no LLM/network shape — it's a pure DB transform of `raw_signals` → `signal_text` and is exercised by every Stage 1 test fixture, so it doesn't need its own runbook. Stage 2 (task 011) has not been built yet; it has its own runbook + scheduler-integration sequence in its future.

Operationally: once scheduler is live, the inbox stops being a static "11 candidates from runbook 001" snapshot and starts growing organically. That's the actual cadence we need to validate before any of the deferred N=100+ items become measurable.

**Specific questions or risks.**

1. **Task ordering: 012 scheduler vs 011 Stage 2 demand check first?** Task 012 says it depends on 005-011 but explicitly allows "jobs for missing sources can be left out and added later" — so I can ship 012 with Stage 2's job slot empty and add it when 011 lands. The argument for *012 first* is that it produces flowing data for the live inbox (operator value compounds); the argument for *011 first* is that demand check is the next *content* layer (filters Stage 1's output for paid-API-worthiness before HITL — Stage 1 already has 11 candidates queued so it's the bottleneck signal-wise, not the source). My instinct is 012 first; my concern is whether unfiltered Stage 1 candidates flooding the inbox at hourly cadence will make HITL noisier than the runbook-001 sample suggests.

2. **Container hygiene: sqlite3 in Dockerfile + post-rebuild gh-auth checklist.** Per feedback 015 this is still pending. The host's `Dockerfile` lives outside `/workspace` at `/srv/claude/apfun.online/Dockerfile`; I can't edit it. Operationally I'm working around the missing `sqlite3` binary by `apt-get install`-ing per-container, which is lost on rebuild. The post-rebuild gh-auth re-ritual is also a real friction — last container rebuild required re-running `gh auth login` before I could open PR #13. **Ask:** is the right move to (a) bundle a one-shot Dockerfile-edit-and-rebuild session with the operator now (clean cutover, but interrupts task 012), (b) defer until the next natural container-rebuild trigger surfaces (continue eating the friction, accept that PRs require re-auth), or (c) something else (e.g., a `scripts/post-rebuild-bootstrap.sh` that installs sqlite3 + walks `gh auth login` so at least the runbook is one command)?

3. **Scheduler interval calibration.** Task 012 prescribes Reddit/HN every 6h, ProductHunt/IndieHackers daily, Stage 1 every 2h. These were written before the runbook 001 timing data. Runbook 001 showed Stage 1 takes ~1-2s wall time for 11 signals → 11 candidates at $0.013 total. Even at 100× scale we're under 1 minute and ~$1.30 per Stage 1 run. **Ask:** should I follow task 012's prescribed intervals as-is, or is there a case for tighter Stage 1 cadence (every 1h?) now that we know the cost shape? Same for the ingest jobs — 6h might be overcautious given how little data HN ingestion captured per run.

4. **Stage 2 (task 011) runbook scope.** When task 011 lands, the convention says it needs a runbook before scheduler integration. Stage 2 hits pytrends and Google autosuggest — both free, both rate-limit-prone, both not previously exercised. The minimal runbook would be ~5 approved-from-inbox candidates pushed through demand check, capture verdicts + observed rate-limit behavior. **No ask yet — flagging now so we can shape task 011's PR around that requirement when it comes up.**

5. **Inbox UI extensions worth bundling vs deferring.** The nav has stubs for `/opportunities`, `/sources`, `/projects` that currently 404. Tasks 020 + 021 cover them. They're not blockers — the inbox is the only HITL surface that matters until Stage 5 ships — but the 404s look bad in the browser. **Ask:** bundle a follow-up PR adding empty placeholder routes (just to remove the 404s) before task 012, or accept the 404s until tasks 020/021 land in their natural sequence?

**Relevant files/diffs.**

- PR: https://github.com/peeeev/apfun/pull/13 (merged as `928357e`)
- Convention added: `CLAUDE.md` line ~143 "Every pipeline stage requires a runbook before scheduler integration"
- Lesson Learned added: `CLAUDE.md` line ~217 "Synthetic tests don't catch surface-changing bugs"
- Task 012 spec: `docs/tasks/012-scheduler.md` (M complexity)
- Task 011 spec: `docs/tasks/011-stage2-demand-check.md` (M complexity)
- Live state: 11 candidates pending in `data/apfun.db`, all from runbook 001, all rendered on `/inbox`
- Container friction: feedback 015 row in `docs/orchestrator/INDEX.md`; host Dockerfile at `/srv/claude/apfun.online/Dockerfile` (operator-owned, outside `/workspace`)
