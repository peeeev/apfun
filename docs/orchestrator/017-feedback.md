# Feedback 017 — empirical input before 011-vs-013 routing

**Date:** 2026-05-21
**Request:** 017-task-011-vs-013-sequencing.md
**Outcome:** Q4 (real-data run first) confirmed. Q1/Q2/Q3 deferred to request 018 with empirical input. New convention: operator runbooks for "execute and capture" sessions.

## The meta-shape

Your three sequencing questions are nested under Q4 — empirical input changes the answer to all three. The right move is to gate Q1-Q3 on the data, not to pre-commit.

## Q4 — empirical-input-first: confirmed (a)

Do the real-data run before deciding between 011 and 013. Reasoning:

1. **Reviewability is genuinely empirical.** "Are these clusters worth a human reviewer's attention?" doesn't have a first-principles answer. It has a "look at 10 of them and decide" answer. Trying to reason about it abstractly is the kind of pre-architecting we've avoided.
2. **Cost validation is overdue.** Feedback 016 already requested an orchestrator turn with `llm_runs` numbers. Catching a wrong PRICING constant on a 5-candidate batch is far cheaper than catching it on the 50th automated batch.
3. **30-60 minutes of operator work is rounding error** against the wrong-task-for-two-days alternative.
4. **The answer to Q1/Q2/Q3 might shift entirely once real data exists.** If clusters are mostly garbage, 011 (filter upstream) becomes obvious. If they're mostly clean, 013+014 (review them) becomes obvious. Routing without traffic data is what we're trying to avoid.

## What the runbook should produce

Three artifacts, in priority order:

### 1. Representative sample of `candidates` rows (~10)

Each with `problem_statement`, `suspected_user`, `seed_keywords_json`, contributing signal count, raw text of 2-3 contributing signals. Pick median, two best-looking, two worst-looking. Enough to answer "are they reviewable" without exhaustive enumeration.

### 2. `llm_runs` aggregates

Per task (`dedup`, `cluster`, `cluster_merge`):

- Total calls
- Mean/max input tokens
- Mean/max output tokens
- Mean/max thinking tokens (or output as proxy)
- Cache hit ratio
- Total `est_cost_usd`

Compare against feedback-016 thinking budgets (`cluster=4000`, etc.) and PRICING expectations.

### 3. Operational observations

Free-form. Parse failures? Retries fire? Caps hit? Prompt-output mismatches in logs? "Things noticed while running it that aren't in the structured output."

## Runbook shape

Keep it tight. Alex is technical but doesn't want ceremony.

Approximate sequence:

1. Set env vars: `APFUN_ANTHROPIC_API_KEY`, `APFUN_REDDIT_USERNAME` (any real handle)
2. `cd /workspace && make init-db && uv run python scripts/seed_sources.py`
3. Run a small Reddit ingest manually against 2-3 subreddits (limit per-sub fetch)
4. Run `normalize_raw_signals`
5. Run `cluster_signals` *directly* (not `replay_clustering.py` — we want persistence so 013+014 has display data)
6. Dump artifacts via SQL queries against `candidates` and `llm_runs`

**Cap the ingestion small.** 2-3 subreddits × 25 posts = ~50-75 raw signals → ~40-60 signal_text rows → ~5-15 candidates. Enough to see cluster quality, not enough to burn meaningful budget.

**Budget guard:** mental cap of $5 for the entire run. 50 signal_text rows through Haiku + ~10 buckets through Opus xhigh shouldn't approach that — verify mid-run. If `llm_runs.est_cost_usd` total approaches $5, stop and diagnose before continuing.

## Pre-committed routing branches (so request 018 is fast)

To avoid a circular "let's discuss the data, then discuss what to do about it" loop, here's the routing matrix:

- **If clusters are clearly reviewable (~70%+ feel real, problem statements grounded in signals, contributing IDs valid):**
  - Q1 = take the nudge
  - Q2 = bundle 013+014 in one PR
  - Q3 = defer 011 until after inbox ships
- **If clusters are noticeably noisy (lots of obvious junk, partial hallucinations, weak problem statements):**
  - Q1 = defer the nudge
  - Q2 = moot
  - Q3 = ship 011 first (filter noise before exposing to humans)
- **If clusters are unusable (mostly nonsense, frequent hallucinations, wrong contributing_signal_ids):**
  - Neither 011 nor 013 is next. Prompt iteration on `cluster.j2` is the next task. Could even rollback some plumbing.

These are my prior heuristics — actual data might suggest a fourth bucket. Don't be bound by them; let the data inform the call.

## On the runbook as a deliverable

Make the runbook a real artifact, not a chat comment. Save to `docs/operator/runbooks/001-stage1-first-pass.md`. Future-you will want to re-run this kind of one-off (probably with bigger inputs) when calibrating future stages; a documented runbook makes it mechanical.

**New convention** — add to CLAUDE.md → Conventions:

> **Operator runbooks.** Short numbered procedures for "execute and capture" sessions live in `docs/operator/runbooks/`. Each runbook lists commands, expected outputs, and what artifacts to bring back to the orchestrator. Used when a design decision requires empirical input rather than first-principles reasoning.

Sibling category to `docs/orchestrator/`. Orchestrator files are "reasoning we did"; runbook files are "things we ran." Both decision-history.

## What request 018 should contain

When you come back with results:

- The 10 sample candidates (raw text, not summaries — let the orchestrator judge quality directly)
- `llm_runs` aggregates table
- Operational observations
- Updated Q1-Q3 recommendation based on what you saw
- Cost-validation outcome (does PRICING come out where feedback-016 expected?)

**Don't pre-answer Q1-Q3** in request 018; surface the data and let the orchestrator decide alongside you.

## Action items

### Claude Code

1. Write `docs/operator/runbooks/001-stage1-first-pass.md` per the shape above. Concrete commands, small batch size (2-3 subs, ~25 posts each), explicit budget guard.
2. Add CLAUDE.md → Conventions snippet for operator runbooks.
3. Commit to the existing `feature/orchestrator-017-next-task-sequencing` branch (docs-only PR).
4. **Stop after the runbook is committed.** Don't start task 011 or 013 yet.

### Operator (Alex)

5. After the runbook PR merges, execute it. Capture artifacts.
6. Open orchestrator request 018 with the empirical data. We decide 011 vs 013 vs prompt-iteration based on what you see.

## Next step

Runbook PR → operator run → request 018 with data → routing decision → next task.

The 30-60 minutes of operator work is the cheapest learning the project will buy this week. Worth doing.

## Meta note

This is the first orchestrator turn that explicitly *declines* to answer the question asked. Worth noting because the discipline is part of the convention: when the right answer is "we need data, not reasoning," the orchestrator's job is to surface that and define what data to collect — not to over-reason.

The "pre-committed routing branches" section is the corollary: declining to answer now doesn't mean punting the work. It means setting up the next turn to be cheap.
