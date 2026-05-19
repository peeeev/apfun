# Feedback 010 — task 005 spec final calibrations

**Date:** 2026-05-18
**Request:** 010-task-005-spec-final.md
**Outcome:** Four short answers. Three small items to bake into task 005 implementation (no separate prep commit needed). Proceed to implementation.

## Answers

### Q1 — Fixture refresh cadence: opportunistic (option a)

**Confirm (a).** Calendar-based refresh schedules become busywork that nobody actually does, then the convention quietly decays. Test-driven refresh keeps the discipline aligned with reality: fixtures get updated when there's a *reason* (something broke, developer is touching that area anyway).

The contract test itself is the trigger. When it fails → investigate → refresh is part of the investigation.

**Refinement:** when refreshing a fixture, update the comment header with the refresh date AND reason:

```json
{
  "_fixture_meta": {
    "captured": "2026-05-18",
    "refreshed": "2026-08-12 — Reddit added media_metadata field; updated contract assertions to match",
    "source": "GET https://www.reddit.com/r/programming/.json"
  },
  ...
}
```

Three-line audit trail per fixture is enough to reconstruct history later. Strip in tests like we do for `_meta_note`.

Formalize only if drift actually bites. Don't pre-engineer for a problem you haven't seen.

### Q2 — `consecutive_failures` increment scope: terminal-only + UA-block guard

Your split (increment on 403/404, not on 5xx/429/timeouts) is correct. Reasoning is sound: 5xx and 429 are about us or Reddit, not about the sub being dead. Conflating produces false-positive disables.

**Refinement 1:** add 410 Gone to the terminal set:

```python
TERMINAL_STATUSES = frozenset({403, 404, 410})
```

Rare in practice but explicitly "this resource is permanently gone" — even more terminal than 403/404.

Everything else (5xx, 429, timeouts, connection errors) logs but doesn't increment. Reset `consecutive_failures = 0` on any successful fetch.

**Refinement 2 — UA-block guard:** when a subreddit *exists* but returns 403 because of UA blocking, that's a *global* problem (UA malformed/blocked across all Reddit), not a per-source one. If >50% of sources in a single batch return 403, treat as global block — log at ERROR, surface in `scheduler_runs`, and **do not increment per-source counters** for that batch.

```python
# heuristic 2026-05-18 — UA-block detection: if >50% of sources in one
# batch return 403, treat as global block. Don't increment per-source
# counters or auto-disable; the issue is our UA, not the subs.
```

Avoids auto-disabling 10 subreddits because Reddit blocked your UA for an hour.

### Q3 — CI permissions: read-only is correct

**`contents: read` only.** Auto-capturing fixtures in CI sounds tempting but is a real footgun — captured fixtures encode "what the third party returned, which is now treated as ground truth." Auto-committing means a flaky API response becomes the test contract silently.

**Flag in `docs/tasks/023-github-actions-ci.md` → Notes:**

> `contents: read` only. If we ever auto-commit captured fixtures, it must be gated behind `workflow_dispatch` with human review of the diff before merge. The fixture-as-contract pattern requires a human review moment — automation would silently launder API flakiness into the test suite.

Future-you reads that and remembers why.

### Q4 — `# verified` URL resolution: TODO verify with task-scoped resolution

Your plan (`# TODO verify <best-known-source>` with a deadline) is fine. No hard-fail needed. The convention's purpose is to surface unverified values for review, not to gate work on URL availability.

**Refinement:** use *trigger conditions* instead of dates for the deadline:

```python
# TODO verify by end of task 005: Reddit's unauth QPM ceiling.
# Source candidates checked, no authoritative current page found.
# Fallback: 10 QPM per community thread <link>.
_REDDIT_UNAUTH_QPM = 10
```

Task-scoped TODOs force resolution within the natural work boundary instead of accumulating across the project. **Grep `# TODO verify` at task end → must be zero**, or escalated as a blocker.

Add to CLAUDE.md → Conventions as a postscript to the verify-constants rule:

> **TODO verify resolution.** When a `# verified` URL can't be sourced in-PR, use `# TODO verify by end of task <NNN>: <reason>` instead. Grep for `# TODO verify` at task end; result must be zero, or the unresolved items get escalated to the orchestrator before the task closes.

## Action items

Fold into task 005 commits (no separate prep commit needed):

1. **Fixture header comment template** with refresh date + reason. Stripped in tests like `_meta_note`. (Q1)
2. **`TERMINAL_STATUSES = frozenset({403, 404, 410})`** for auto-disable increment, plus the >50%-of-batch UA-block heuristic with full `# heuristic` annotation. (Q2)
3. **CLAUDE.md addition** for task-scoped `# TODO verify` resolution discipline. (Q4)

Q3 is a one-line Notes addition in `docs/tasks/023-github-actions-ci.md`, also fold in naturally.

## Next step

Implement task 005. Two-commit plan looks right:

- `005a: rate limiter + sources.consecutive_failures column` — `apfun/sourcing/_rate_limit.py`, alembic migration, unit tests for `TokenBucket`.
- `005b: Reddit ingester` — `apfun/sourcing/reddit.py`, config additions, bootstrap script, unit tests against fixtures, schema contract test, integration test that captures the live fixture.

After implementation, orchestrator request summarizing what landed and any surprises — especially anything you couldn't `# verify` and had to `# TODO verify`. Those are the highest-signal items for review.

## Meta note

The Cross-cutting section in `docs/tasks/000-overview.md` (for task 023 with its non-numeric execution position) is a small but important structural improvement. The pure numbered sequence works until a task doesn't fit the linear flow; that's where a category like Cross-cutting starts earning its keep. Pattern to use again if other infra-shaped tasks emerge.
