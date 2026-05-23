# Request 023: task — `/ops` operator dashboard

**Date:** 2026-05-23

**Context.** The funnel runs largely on its own now: scheduler fires ingests + pipeline jobs, candidates accumulate, costs accrue. The operator currently has no at-a-glance view of system health — diagnostics happen by SSH'ing into the container and running ad-hoc `sqlite3` queries against `scheduler_runs`, `llm_runs`, `sources`, etc.

This was acceptable while building. It's no longer acceptable while operating. The operator-experience friction surfaced today during the post-runbook-003 verification — we discovered `pipeline.cluster` had a `next_run_time` in the past (apparent scheduler bug) only because of a manual diagnostic session. A standing dashboard would have surfaced it without prompting.

**Goal.** A single web page at `apfun.online/ops` showing the operator everything they need to know about funnel health in 30 seconds of glancing. Read-only; no mutations; minimal LLM calls (none — all data from local DB).

## Scope

**In scope:**

- New route `/ops` in `apfun/web/routes/`. Server-rendered HTMX + Jinja, matching the existing inbox-style page conventions.
- Reuse `_base.html` chrome. Add a nav link to `/ops` next to the Inbox link.
- Behind the existing Apache basic-auth (no app-level auth changes needed; the vhost already protects everything under apfun.online).
- Six sections (detail below). One page, scannable layout, no tabs.
- Auto-refresh the *body* of the page (not the whole page) every 30 seconds via HTMX `hx-trigger="every 30s"`. The chrome and nav stay stable.

**Out of scope:**

- Mutating actions (no "restart job," no "force-fire cluster"). Read-only only.
- Drill-down views. Single page; no `/ops/scheduler/<job_id>` detail routes.
- Time-window pickers. Hardcoded windows where useful (today / last 7d / lifetime); revisit if/when needed.
- Charts/graphs. Tables and numeric cards only. Chart libraries are added complexity for marginal value at this scale.
- New schema changes. All data from existing tables (`scheduler_runs`, `llm_runs`, `sources`, `raw_signals`, `signal_text`, `candidates`, `apscheduler_jobs`).

## The six sections

Order matters — most-glance-worthy at top.

### 1. Top summary cards (4-6 KPIs at-a-glance)

Single row of cards, each a single number with label and (where useful) a small status indicator:

- **Pending candidates** — `SELECT COUNT(*) FROM candidates WHERE decision = 'pending'`. Click-through (if cheap to implement) goes to `/inbox`.
- **Today's cost** — `SELECT SUM(est_cost_usd) FROM llm_runs WHERE date(created_at) = date('now')`.
- **Last-7d cost** — same, rolling 7 days.
- **Unprocessed signals** — `(SELECT COUNT(*) FROM raw_signals) - (SELECT COUNT(*) FROM signal_text)`. Number of raw_signals waiting for normalization. Healthy state: ≈0. Significant non-zero = pipeline backlog.
- **Active sources** — count where `is_active = TRUE`, grouped by kind in a small inline format like "reddit:3 hn:3 ph:1 ih:2 review:3".
- **Lifetime cost / candidate** — `total_cost / total_candidates`. Sanity check on per-discovery cost.

### 2. Scheduler — job calendar with staleness warnings

Table of all registered APScheduler jobs from `apscheduler_jobs`:

| job_id | next_run_time | status |
|---|---|---|
| reddit.ingest_batch | in 4h 12m | ✓ scheduled |
| pipeline.normalize | in 7m | ✓ scheduled |
| pipeline.cluster | **3h 22m ago** | ⚠ STALE |

`status` column:
- `✓ scheduled` — next_run_time is in the future
- `⚠ STALE` — next_run_time is in the past by 5+ minutes (the bug we found today)
- `⏸ disabled` — not in `apscheduler_jobs` but registered in code (means job was unregistered or never restored from jobstore)

The `STALE` warning is the high-value diagnostic — it would have caught today's `pipeline.cluster` issue without manual digging.

### 3. Recent scheduler runs

Last 20 rows from `scheduler_runs` ordered by `started_at DESC`. Columns: `started_at`, `job_id`, `ok`, `items_processed`, `error` (truncated to ~80 chars if present).

Visually highlight `ok=False` rows in red. Hover on truncated error text reveals the full message (CSS-only; no JS).

### 4. Sources health

Grouped by kind, list of sources with their key health fields:

```
reddit (3 active, 0 disabled)
  r/SaaS              consecutive_failures: 0  last_ingest: 2h ago  ✓
  r/Entrepreneur      consecutive_failures: 0  last_ingest: 2h ago  ✓
  r/smallbusiness     consecutive_failures: 1  last_ingest: 4h ago  ⚠

hn (3 active, 0 disabled)
  ...
```

`last_ingest` derives from the most recent successful `scheduler_runs` row for that source's batch job — approximate but useful.

`⚠` if `consecutive_failures >= 1`; `✗` and red if `consecutive_failures >= 3` (the auto-disable threshold from feedback 010).

### 5. LLM cost breakdown

Two side-by-side tables:

**By task (lifetime):**

| task | calls | avg_cost | total_cost |
|---|---|---|---|
| cluster | 47 | $0.012 | $0.564 |
| dedup_signal | 312 | $0.001 | $0.312 |
| score | 0 | — | — |

**Last-7d cost by day:**

| date | calls | cost |
|---|---|---|
| 2026-05-23 | 78 | $0.18 |
| 2026-05-22 | 134 | $0.31 |
| ... | | |

Cache hit ratio surfaced inline somewhere visible — `SUM(cache_read_input_tokens) / SUM(cache_read_input_tokens + cache_creation_input_tokens)`. Currently expected to be 0% (the deferred cache-wiring item from feedback 018); when it changes, this is where you'll notice.

### 6. Recent errors

Two compact tables, last 24 hours only:

**`scheduler_runs` errors:** `WHERE ok = FALSE AND started_at > datetime('now', '-24 hours')`. Columns: time, job_id, error.

**`llm_runs` errors:** `WHERE ok = FALSE AND created_at > datetime('now', '-24 hours')`. Columns: time, task, attempts, error.

Empty-state for both: "No errors in last 24h ✓".

## Implementation notes

- **Single SQL query per section where possible.** Don't N+1 against the DB. The whole page should render in well under 100ms; if any section's query needs a CTE or join, that's fine.
- **All times displayed as relative ("4h 12m ago", "in 7m")** with a UTC timestamp tooltip on hover. Operators don't think in UNIX timestamps.
- **No external CSS frameworks.** Use the existing Tailwind subset from task 013/014. The page should visually match the inbox style.
- **HTMX auto-refresh on the body only.** Page chrome stays stable; only the data area refreshes. Use `hx-get="/ops/body"` (internal partial route) on a 30s trigger. Or, if simpler, refresh the whole page via meta-refresh — slightly less elegant but no partial-route plumbing.
- **Pre-empt the "what if the DB is locked" case.** SQLite read locks during heavy concurrent writes can briefly block. Wrap query execution in a try/except that renders a "Database temporarily busy — refresh shortly" placeholder rather than 500-ing.

## Tests

Smaller test surface than the inbox — it's mostly read-only display:

- Unit test per section that renders against a stub DB with known fixtures (use the existing test-DB pattern).
- Test that STALE warnings fire correctly: a job with `next_run_time = now - 1h` produces a STALE indicator; one with `next_run_time = now + 1h` doesn't.
- Test that error sections are empty-stated when there are no errors.
- Test that auto-refresh attribute is present on the body element.

No integration tests needed — page has no external dependencies.

## Documentation updates (same PR per the docs-update convention)

1. **`docs/operator/SETUP.md`** (or wherever operator docs live) — add `/ops` to the list of available URLs alongside `/inbox`.
2. **`docs/tasks/` index** — add the task file (number TBD, see "Specific questions" below).
3. **`docs/orchestrator/INDEX.md`** — row 023 → answered after PR merges.
4. **`README.md`** if it has a feature list — mention the dashboard.

No CLAUDE.md changes needed unless an unexpected convention surfaces during implementation.

## What I would do next without intervention

1. Branch `feature/task-NNN-ops-dashboard` (see Q1 below for the number).
2. Create `apfun/web/routes/ops.py` with the single route and one partial route for HTMX body refresh.
3. Create `apfun/web/templates/ops.html` with the six sections.
4. Add nav link to `_base.html`.
5. Write the six SQL queries (or sqlalchemy equivalents) — one per section.
6. Add tests per the Tests section.
7. Open PR. Verify in browser before requesting review.

## Specific questions or risks

1. **Task number.** The /ops dashboard wasn't in the original task plan. Three options:
   - Use the next sequential number after the highest in `docs/tasks/` (probably 024 or 025).
   - Re-use task 022 — the original "digest email" was scoped for after Stage 5 ships and is unlikely to actually be built; /ops effectively supersedes it for operator-visibility purposes.
   - Call it `chore-ops-dashboard` without a number (like the `chore/inbox-nav-placeholders` pattern from feedback 019).
   - **My recommendation: option 1, next sequential number.** /ops is real meaningful work, not a chore; deserves a task number. Leave 022 alone in case the digest-email lane is wanted later.

2. **The pipeline.cluster STALE issue we found today.** This dashboard *surfaces* the issue but doesn't *fix* it. Worth a separate small follow-up turn to diagnose why pipeline.cluster's next_run_time is in the past and how to recover. Don't bundle the fix into this PR — keep this PR focused on the read-only dashboard.

3. **Auth.** No app-level changes needed. Apache htpasswd already protects everything under apfun.online. Mention this in the docs so future-you doesn't accidentally try to add a second auth layer.

4. **Refresh cadence.** 30 seconds is opinionated. If it feels too aggressive (visual flicker, DB load) or too slow (operator notices things late), it's a one-line config change. Don't over-think it now; ship with 30s and adjust if needed.

5. **Mobile rendering.** Should this look right on a phone? Tables don't typically. **My recommendation: design for desktop browser only.** This is an operator dashboard, not a customer interface. Mention in the PR description so it doesn't surprise anyone.

## Relevant files

Code under change:
- `apfun/web/routes/ops.py` — new file
- `apfun/web/routes/__init__.py` — register the new route
- `apfun/web/templates/ops.html` — new template
- `apfun/web/templates/_base.html` — add nav link
- `tests/unit/test_ops_route.py` — new test file

Docs under change:
- `docs/operator/SETUP.md` — mention /ops
- `docs/tasks/NNN-ops-dashboard.md` — new task file (number per Q1)
- `docs/orchestrator/INDEX.md` — row 023 → answered

## Meta note

This task is a quality-of-life upgrade for the operator. It doesn't move the funnel forward per se — no new ingester, no new stage, no new candidates produced. But it dramatically reduces the cost of operating what's already built. The "I forgot my htpasswd / had to SSH in to diagnose a stuck scheduler / didn't know task 012 was merged" friction patterns all point at the same gap: there was no surface where the system's state was visible at a glance.

Operator UX is a real concern for personal-use projects, not just commercial ones. /ops is the small investment that prevents friction from compounding into "I haven't checked on apfun in a week."
