# Request 017: next task sequencing — 011 (Stage 2 demand) vs 013 (admin UI inbox)

**Date:** 2026-05-21

**Context**: Task 010 (Stage 1 clustering) merged via PR #8. Five ingesters produce `raw_signals`, `pipeline.normalize` projects them into `signal_text`, and `pipeline.cluster` turns batches of those into `candidates` with `decision='pending'`. The pipeline now has its first real artifact downstream stages will consume.

Feedback 016 closed with this nudge:

> After 010 ships, consider prioritizing task 013 (admin UI inbox) over task 011 (Stage 2 demand check). Reason: until HITL is exercised, candidates accumulate without review and we don't learn whether clusters are even reviewable. The admin UI is where clustering quality becomes legible. Demand check matters too but operates on already-approved candidates, so it has no leverage on the unknown question ("are my Opus clusters good enough to be worth reviewing?").

Raising as a deliberate orchestrator turn before starting either.

## What landed in task 010 (orchestrator can't see PR #8)

### Schema + wrapper extension (prep commit `9494aa7`)

- **`apfun/llm/client.py`** — `judge_json()` / `mechanic_json()` (Pydantic schema validation; `JSONParseError` integrated into the retry loop alongside transient API errors; truncated raw response logged into `llm_runs.error` on final-attempt failure). `cache_ttl: Literal["5m", "1h"]` knob on `judge()`/`_build_system`. `PRICING` renamed `cache_write` → `cache_write_5m` and added `cache_write_1h = 10.00` for Opus 4.7. `# verified 2026-05-21`.
- **`apfun/models/candidate.py` + Alembic `47bc83859243`** — `candidate_signals.created_at` column with `server_default=CURRENT_TIMESTAMP`. Enables "N signals since rejection" UI computation (Q5) + manual-re-cluster mechanism via row deletion (Q8).
- **`CLAUDE.md`** — new "HITL decisions are durable" convention.
- **`docs/tasks/010-stage1-clustering.md`** — rewritten end-to-end around `signal_text`.

### Stage 1 implementation (commit `10740d0`)

- **`apfun/pipeline/cluster.py`** — `cluster_signals(session, *, llm_client) -> ClusterResult`:
  - Reads unclustered `signal_text` (skip `is_low_signal=True`, skip rows in `candidate_signals`).
  - Haiku pre-pass per signal → `SignalCoreComplaint(core_complaint, vertical, keywords)`.
  - Bucketing by `(vertical, frozenset(keywords))` with normalization (case/whitespace/dedupe) — deterministic across input order.
  - Opus per-bucket with `cache_ttl="1h"` → `ClusterOutput.clusters: list[IdeaCard]`. Hallucinated `contributing_signal_ids` dropped with a log entry, not persisted.
  - `dedup_key` collision (slug of `problem_statement`) links new signals to the existing candidate **without auto-flipping decision** — the "HITL decisions are durable" convention from feedback 016 Q5.
  - Caps: `_MAX_BUCKETS_PER_RUN=50`, `_MAX_SIGNALS_PER_RUN=500`. Largest-first when bucket-capped; `result.capped` flag exposed.
  - `_run_pass_2_merge` scaffold present but not yet wired (single-bucket-over-150k-tokens is rare for v1 volumes).
  - Writes `scheduler_runs(job_id="pipeline.cluster")` row per invocation.
- **Prompts** — three Jinja templates under `apfun/llm/prompts/`:
  - `dedup_signal.j2`: Haiku pre-pass — strict JSON, lexicographic-order keywords.
  - `cluster.j2`: Opus pass-1 — "DON'T invent ideas not present", "EVERY contributing_signal_id must be in input batch", empty-clusters is an acceptable output.
  - `cluster_merge.j2`: Opus pass-2 — operates on titles + keywords only, returns merge_map dict.
  - Loader uses `StrictUndefined` so prompt bugs fail loud.
- **`scripts/replay_clustering.py`** — read-only prompt-iteration tool. Doesn't persist; loads signal_text by id or `--all`, runs Haiku + Opus, prints JSON to stdout.

### Tests

- **26 new unit tests** (16 cluster pipeline + 10 JSON wrapper). 195 total unit tests pass. Coverage explicitly pins all eight feedback-016 invariants — bucket determinism, dedup-to-rejected without decision flip, idempotency, cap behavior (largest-first), hallucinated-id drop, `cache_ttl="1h"` plumbing, JSONParseError retry within budget, final-attempt truncated response logging.
- **Integration test** (`tests/integration/test_cluster_live.py`, gated on `APFUN_ANTHROPIC_API_KEY`) hits real Anthropic on 5 hand-crafted fixture signals; asserts ≥1 candidate with linked signals.

### Stats

- 0 pyright errors, ruff clean, `grep -r '# TODO verify'` returns zero.
- LLM wrapper backward-compatible — 17/17 existing client tests pass unchanged.

### Post-task-010 followup (still pending)

The "open an orchestrator request after first scheduled Stage 1 run with `llm_runs` cost numbers + bucket distribution" item from feedback 016 hasn't been done yet — would require operator setup (env vars, run migrate/seed/ingest/normalize/cluster) ahead of any production data being available. Defer until either (a) we wire 012 scheduler + run live data, or (b) until task 010's prompts get their first real refinement round.

## The sequencing question

Both tasks have specs. Both are M complexity.

### Task 011 — Stage 2 demand check (`docs/tasks/011-stage2-demand-check.md`)

- Goal: cheap kill/keep filter on `candidates` using Google Trends (`pytrends`) + autosuggest scraping.
- For each candidate without a `demand_checks` row: take top 1-3 seed keywords, compute 12-month trend slope, fetch autosuggest, write a `demand_checks` row.
- Verdict: `fail` if all slopes < -0.2 AND no "alternative to" / "vs" / "best" patterns in autosuggest → `decision='auto_killed'`. `pass` otherwise → leaves `decision='pending'`.
- Always persists raw slope + autosuggest into `demand_checks.autosuggest_json` for retuning.
- Acceptance includes an integration test against real Trends API.

**What this changes operationally:** candidates with bad signal patterns get auto-killed silently; HITL inbox only sees survivors.

### Task 013 — Admin UI base (`docs/tasks/013-admin-ui-base.md`)

- Goal: server-rendered HTMX + Jinja + Tailwind base. Shared layout, dark-mode-default, nav stub (Inbox / Opportunities / Sources / Projects).
- Tailwind via standalone CLI binary (no Node toolchain).
- `_base.html` page chrome + HTMX CDN script (pinned + SRI'd).
- `index.html` redirecting to `/inbox` placeholder.
- `/healthz` moves into the web router.
- Acceptance: `GET /` → 200 base layout, `GET /static/app.css` returns built CSS.

**What this changes operationally:** there's a URL to look at the funnel through. Still no inbox UI until 014 (the *actual* inbox endpoint that lists candidates and accepts approve/reject), but the chrome + nav exists.

**Critical observation**: task 013 alone does NOT make HITL "exercised" — it's just the scaffolding. The inbox endpoint that actually surfaces `candidates` is **task 014**, which depends on 013. So the meaningful comparison isn't really "011 vs 013," it's "011 vs (013 + 014)."

### Specific questions

1. **Confirm or push back on the feedback-016 sequencing nudge.**
   - **My lean: take it.** With clustering shipped and no inbox, candidates accumulate as inert DB rows. The unknown that matters most right now is "are Stage 1's clusters even reviewable?" — and the only way to find out is to look at them in a UI. Demand check on its own has no leverage on that question because demand check operates *upstream* of HITL.
   - **Counter-argument worth raising:** demand check filters out garbage *before* it hits the inbox. If clustering produces a lot of `auto_killed`-worthy candidates, demand check first means HITL doesn't waste eyeballs on them. But: until we *see* what clustering produces, we don't know whether the garbage rate is 0%, 50%, or 95%. The conservative move is to look first.

2. **If we take 013, should we bundle 014 (inbox endpoint) in the same PR?**
   - 013 alone is a UI shell with no real content — nothing to click, nothing to learn from.
   - 014 makes it real: list pending candidates, approve/reject buttons, "N signals since rejection" computation surfaces.
   - **My lean: bundle 013 + 014 in one PR.** The 013 spec is small (page chrome + Tailwind build); 014 is where the actual learning happens. Splitting them means a useless intermediate merge.
   - Alternative: ship 013 fast as a "skeleton" merge, then 014 as a follow-up. Cleaner PR cadence but two merges for one feature.

3. **Stage 2 demand check (task 011) — should we move it later?**
   - It's still important — the funnel needs the auto_killed signal eventually.
   - But it doesn't have to land *before* the inbox exists. We can run Stage 1 → eyeball clusters → ship the inbox → then add demand check between Stage 1 and the inbox.
   - **My lean: defer 011 until after 013+014 ship**, with the rationale that demand check's value depends on knowing what clustering produces (so we know what to kill).

4. **Should we run the funnel end-to-end on real data BEFORE starting the next task?**

   Feedback 016 already requested an orchestrator request with `llm_runs` cost numbers + bucket distribution after the first scheduled Stage 1 run. That request is still pending — nothing has been ingested + clustered against real data yet. Two reads on timing:

   - **(a) Do the real-data run FIRST, then decide.** 30-60 min of operator work: set `APFUN_REDDIT_USERNAME` + `APFUN_ANTHROPIC_API_KEY`, `make init-db`, run `scripts/seed_sources.py`, run a Reddit ingest manually, run `normalize_raw_signals()`, run `cluster_signals()` via `scripts/replay_clustering.py` or a one-off Python session. Eyeball `candidates` rows + cost numbers. **THEN** write a richer orchestrator request that combines (a) cost validation + (b) the 011-vs-013 sequencing question with empirical input ("looking at these N candidates, are they reviewable?").

   - **(b) Pick 011 or 013 now, defer the real-data run.** Faster. We rely on the orchestrator's a-priori reasoning to pick the next task. The cost-numbers orchestrator request happens later — probably once 012 (scheduler) lands and Stage 1 starts running automatically.

   **Argument for (a):** the sequencing question itself is partly empirical — "are my Opus clusters even reviewable?" gets a much cleaner answer with 5-20 real candidates in front of us than with first-principles reasoning. The reviewability question is *the* unknown driving the feedback-016 nudge toward 013; doing a real-data run is the cheapest way to actually answer it. Also: the cost-numbers data point should arrive *before* 012 scheduler is wired (which would do this automatically), not after — because if PRICING is wrong, we'd rather catch it on a manual 5-candidate batch than on an automated daily batch.

   **Argument for (b):** the operator cost of a manual run is non-trivial (~30-60 min) and the answer might be "obvious from the spec" without empirical input. If the orchestrator's first-principles read on 013 is clear, the manual run is gold-plating.

   **My lean: (a) do the real-data run first.** Reasons:
   - The orchestrator request *after* a real run is more informative (cost numbers + sample candidate quality + clarification on inbox UX) than the abstract one we're writing now.
   - The cost-numbers item from feedback 016 is overdue; doing it now closes a loop.
   - Worst case it confirms (b) was fine; we've still learned what live data looks like.
   - 30-60 minutes of operator work is small vs an M-complexity task's 1-2 day implementation.

   **Open question for the orchestrator:** is the marginal value of seeing real candidates *before* deciding 011 vs 013 worth the operator cost? Or should we just pick a task and ship?

   If you say (a): I write a concrete operator runbook (the exact sequence of commands), the operator executes it, captures `llm_runs` + `candidates` output, I write a follow-up orchestrator request 018 with the empirical input. **No code changes between now and that runbook step.**

   If you say (b): I just start 013 or 011 per whatever sequencing you confirm in Q1-Q3 above.

5. **Anything else worth raising about the post-task-010 state?**
   - `scripts/replay_clustering.py` exists but hasn't been exercised. It's the cleanest tool for (a) above — runs Stage 1 against arbitrary `signal_text` ids without persisting candidates, so the operator can iterate on prompts before any DB state changes.

## What I would do next without intervention

Three branches depending on the answers:

- **If Q4 == (a) real-data run first:** I write `docs/operator/runbook-stage1-first-pass.md` (or similar — a short numbered list of commands), the operator runs it, captures output, and I write follow-up orchestrator request 018 with the cost numbers + a representative sample of the `candidates` produced. No code changes between now and that runbook step.
- **If Q4 == (b) skip real-data run, take feedback-016 nudge (Q1):** cut `feature/task-013-admin-ui-base`. If Q2 says bundle 014, both tasks in the same PR; otherwise 013 standalone with 014 as a follow-up.
- **If Q4 == (b) skip real-data run, push back on the nudge (Q1):** cut `feature/task-011-stage2-demand-check` and implement per the existing spec.

## Relevant files

- branch `feature/orchestrator-017-next-task-sequencing` (this request file only, docs-only)
- `docs/tasks/011-stage2-demand-check.md` — Stage 2 spec
- `docs/tasks/012-scheduler.md` — scheduler spec (depends on both 010 and 011, so always after at least one of them)
- `docs/tasks/013-admin-ui-base.md` — admin UI base spec
- `docs/tasks/014-*.md` would be the inbox endpoint (referenced by 013 but not yet specced beyond the file index)
- `docs/orchestrator/INDEX.md` — row 017 → open after this commit
