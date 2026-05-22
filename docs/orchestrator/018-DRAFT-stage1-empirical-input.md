# Request 018 (DRAFT): Stage 1 empirical input + container hygiene + bug surfaced

**Status:** DRAFT — placeholders for the runbook dump output. Operator runs `docs/operator/runbooks/001-stage1-first-pass.md`, then pastes the artifacts into the marked sections below. Once filled in, rename to `018-stage1-empirical-input.md` and remove this status block.

**Date:** 2026-05-22 (drafted), final on completion

**Context**: Per orchestrator feedback 017, ran runbook 001 to get empirical input before deciding task 011 vs 013. The runbook surfaced a high-severity production bug in the first hour — exactly what feedback 017 said it would buy us. Bug fixed in PR #10 (merged); runbook resumed. This request brings back (a) the empirical artifacts from the post-fix run, (b) the bug report, and (c) container hygiene items that accumulated this session.

## Headline: SAVEPOINT bug found + fixed before any data was lost in production

Surfaced by the runbook's "ingest reported captured=11, fresh-session count=0" diagnostic. Validates the empirical-input-first discipline.

### What was broken

Every ingester's `_insert_signal` used:

```python
session.add(signal)
try:
    session.flush()
except IntegrityError:
    session.rollback()   # ← nukes the WHOLE transaction
    return False
return True
```

`session.rollback()` rolls back the entire transaction. When `ingest_batch` runs N queries against a single source without intermediate commits, a single content-hash collision (extremely common with HN's overlapping search queries) wipes every prior successful insert in the batch. The function returns `False` for the duplicate but the caller's `items_captured` counter — already bumped for now-erased rows — is left wrong. Final batch commit commits nothing.

**Scope:** 6 sites — 5 ingesters (`reddit`, `hn`, `producthunt`, `indiehackers`, `review_sites/_common`) plus `cluster.py::_persist_clusters` (`candidate_signals` link-insert loop).

### Why existing tests missed it

The existing dedup test (`test_dedup_on_second_run`) called `ingest()` twice with `session.commit()` between calls. By the time the second call's collisions fired, the first call's rows were already durably committed. The bug only manifests when novel and duplicate inserts share an *uncommitted* transaction — which is exactly what `ingest_batch` does in production.

### Fix

New `apfun.db.try_insert(session, instance) -> bool` helper that wraps `add` + `flush` in `session.begin_nested()` — a SAVEPOINT. On `IntegrityError`, only the savepoint rolls back; the surrounding transaction (and prior inserts) survives. Returns `True` on success, `False` on UNIQUE collision.

Every fix site collapses to:

```python
return try_insert(session, signal)
```

### Verification

```
ingest reported captured: 11
fresh-session count: 11     # was 11 vs 0 pre-fix
```

5 new regression tests pin the invariant. `test_intra_batch_collision_does_not_destroy_prior_inserts` is the load-bearing one — would fail loudly on the pre-fix code.

### Process insight

The bug existed across 5 ingesters for weeks (since task 005 in early May). It was caught the first hour we ran them against real data. Worth flagging because:

1. **Synthetic tests alone aren't sufficient** for catching transaction-shape bugs in DB-write paths.
2. **The orchestrator feedback-017 read was correct** — "30-60 min of operator work is rounding error vs a wrong-task-for-two-days alternative" turned out to apply to bugs too, not just sequencing decisions.
3. **The empirical-input-first discipline now has an unambiguous case study.** Consider adding a CLAUDE.md Lesson Learned to that effect (suggestion in the action items below).

---

## What landed in tasks 010 + 010a (recap; orchestrator can't see PRs)

Same as request 017's recap — see `docs/orchestrator/017-task-011-vs-013-sequencing.md`. The state has not materially changed except for PR #10's hotfix and the runbook artifacts below.

---

## Empirical artifacts from the runbook

**(Fill from `scripts/dump_run_artifacts.py` output. Paste verbatim — do not summarize.)**

### Candidates — representative sample

[OPERATOR: paste ~10 candidates from the CANDIDATES section of the dump.
Pick 2-3 best-looking, 2-3 worst-looking, 4-5 median. Include full
problem_statement + contributing signals.]

```
[paste here]
```

### llm_runs aggregates

[OPERATOR: paste the entire LLM_RUNS AGGREGATES section. Include the cache
hit ratio line and the GRAND TOTAL COST line.]

```
[paste here]
```

### scheduler_runs

[OPERATOR: paste the SCHEDULER_RUNS section.]

```
[paste here]
```

### Operational observations

[OPERATOR: free-form. Things to consider mentioning:]

- Did `JSONParseError` retries fire? (look for `attempts > 1` in llm_runs)
- Did `result.capped` come back True from cluster_signals? (was bucket count > 50?)
- Anything in the log output that "looked weird"?
- Per-call cost vs expectation: Haiku dedup calls should be ~$0.001 each; Opus cluster calls ~$0.05-$0.20 each. Was that roughly true?
- Were the Stage 1 latencies sensible? (~5-20s per Opus call with thinking)

[paste here]

---

## Container hygiene items (operator-side, accumulated this session)

Bundle these into the Dockerfile/docker-compose update queue. None are blocking but each costs friction for future sessions.

### 1. `sqlite3` CLI missing

The dev container ships without `sqlite3` (CLI). The runbook references it; the operator can `apt-get install -y sqlite3` in the running container but it vanishes on rebuild.

**Proposed Dockerfile addition** (host `/srv/claude/apfun.online/Dockerfile`):

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
        sqlite3 \
    && rm -rf /var/lib/apt/lists/*
```

Place in the system-deps block (before `USER node`).

### 2. `gh auth` ritual after each rebuild — STILL pending from feedback 015

Per feedback 015 action item 4, this was supposed to land in `/srv/claude/apfun.online/README.md` as a post-rebuild checklist. Confirm whether it's there; if not, this is the third session bitten by re-authenticating GitHub. Worth a 2-minute fix.

### 3. `.venv` named-volume drop — DONE per operator this session

The operator confirmed dropping the `.venv` named-volume from `docker-compose.yml` per feedback 015 action item 1. `uv run` works without `UV_PROJECT_ENVIRONMENT=/tmp/apfun-venv` now. No action needed; flagged for completeness.

### 4. Reddit ingest still 403-blocking

During the runbook, all 3 Reddit sources returned 403 → UA-block guard fired. Did NOT diagnose further because HN was a clean fallback for the runbook's purpose. **Open question for the orchestrator:** worth a separate investigation, or do we tolerate Reddit being flaky in dev/test and rely on the auto-disable mechanism + scheduler health UI (task 021) to surface it in production?

Possible causes:
- The `APFUN_REDDIT_USERNAME` value used was a real handle but Reddit IP-blocked the datacenter
- UA format drifted (Reddit changes scraping policy frequently)
- This datacenter IP got rate-limit-banned during a prior session

If we want to keep Reddit working, options include: (a) switch to authenticated OAuth flow (task X), (b) accept Reddit-flaky-in-dev as the cost of using Reddit's free API, (c) deprioritize Reddit and lean on HN+IH+PH+review_sites for v1 signal.

**My lean: (b).** Reddit's free API has always been flaky from datacenter IPs; the auto-disable mechanism + scheduler observability handles this gracefully in production. The cost of OAuth migration is large enough that we should defer until we have evidence the funnel needs Reddit specifically (some niches might be Reddit-only).

---

## The decision still pending: 011 vs 013 (vs prompt iteration)

The pre-committed routing matrix from feedback 017:

| Cluster quality | Next task |
|---|---|
| 70%+ reviewable | 013 + 014 bundled (admin UI + inbox endpoint) |
| Noticeably noisy | 011 (Stage 2 demand check) first |
| Unusable | Prompt iteration on `apfun/llm/prompts/cluster.j2` |

Per feedback 017's guidance: **don't pre-answer the routing here.** Surface the data above and let the orchestrator decide alongside us.

If the data shifts something — e.g., clusters are reviewable BUT cost is wildly higher than PRICING predicted, or thinking budgets are visibly cramped — the orchestrator may want to slot a tuning step before either 011 or 013. We'd rather find out.

---

## Specific questions

1. **Routing decision.** Per matrix above, what's the next task given the candidates + cost data above?
2. **Cost validation.** Did `llm_runs.est_cost_usd` come out where feedback 016's PRICING assumptions predicted? Anything to retune ahead of schedule?
3. **Thinking-budget retune triggers.** Feedback 005 set retune triggers at "50 rows in llm_runs for any single task" or "judge() call hitting >90% budget warning." Were any of those tripped during the runbook? If so, retune now rather than wait for the scheduler era.
4. **Reddit ingest** (per container hygiene #4 above) — accept-and-defer or investigate now?
5. **Lesson Learned for CLAUDE.md.** Suggest adding: "Synthetic dedup tests don't catch transaction-shape bugs in DB-write paths. Tests that mock the `session.commit()` cadence of production code paths (not the cadence the test happens to use) catch a class of bugs synthetic tests miss." Or whatever shape the orchestrator prefers.

## What I would do next without intervention

Per the routing matrix:

- **70%+ reviewable** → cut `feature/task-013-admin-ui-base` (probably bundled with 014 per request 017's Q2 lean).
- **Noticeably noisy** → cut `feature/task-011-stage2-demand-check`.
- **Unusable** → cut `feature/prompt-iteration-cluster` and use `scripts/replay_clustering.py` to iterate against the captured `signal_text` state from this runbook run.

The branch name and first-commit plan depend on the answer to Q1.

## Relevant files

- branch `notes/request-018-stage1-empirical-draft` (this draft file only)
- PR #10 (SAVEPOINT hotfix) — assumed merged before request 018 is filed
- `docs/operator/runbooks/001-stage1-first-pass.md` — the runbook that surfaced everything above
- `scripts/dump_run_artifacts.py` — produces the empirical sections
- `apfun/pipeline/cluster.py` + prompts — the system under test
- `docs/orchestrator/INDEX.md` — row 018 → open after this commit is renamed and posted
