# Feedback 011 — task 005 implementation start + branch-per-task cadence

**Date:** 2026-05-19
**Request:** 011-feedback-010-applied.md
**Outcome:** Four answers + one operational shift. Cadence change for git workflow; design choices for Q1-Q4 all confirmed with refinements. Proceed to task 005 implementation on a feature branch.

## Operational shift — branch-per-task cadence

The push-to-main classifier block is a guardrail working as intended, not a bug. Going forward:

- **Branch per task**, named like `feature/task-005-reddit-ingester`.
- Orchestrator request/feedback files commit on the feature branch.
- Open PR when the task is ready. You (or future-CI from task 023) merge.

Three reasons this composes well with the orchestrator pattern:

1. PRs give changes a moment to be visible before permanent. Cheap insurance.
2. Task 023's CI runs on PRs — operating in that flow already saves churn later.
3. Easier to "what changed in task 007?" via `git log feature/task-007-...` than via main's interleaved history.

**For commit `ef7cfb1` specifically:** docs-only, low risk. Push it via whichever path your access allows (direct-to-main if possible, branch+PR if blocked). Don't let infra friction stall task 005.

## Answers

### Q1 — Pydantic: custom validator with CLAUDE.md-pointing message

**Custom validator.** Your reasoning is right: the self-documenting error message is the value. `ValidationError: field required (type=value_error.missing)` is technically correct and operationally useless at 6am.

Mirror the host validator pattern already established in `apfun/config.py`:

```python
@field_validator("reddit_username", mode="after")
@classmethod
def _validate_reddit_username(cls, v: str) -> str:
    if not v or not v.strip():
        raise ValueError(
            "APFUN_REDDIT_USERNAME is required. Reddit silently blocks "
            "non-conformant User-Agents — an empty username produces "
            "phantom-empty results, not errors. Set the env var to your "
            "Reddit handle. See CLAUDE.md → Networking for context."
        )
    return v.strip()
```

The CLAUDE.md pointer is the operational value — turns a runtime error into a 60-second resolution.

### Q2 — Batch separation: "ingester dumb, scheduler smart" is correct

Per-source `ingest()` returns a result struct; batch wrapper (`ingest_batch`) tallies status codes across the results, applies the UA-block guard, decides counter increments, writes `scheduler_runs`.

Reasons:

- **Single-responsibility:** `ingest()` doesn't need to know what other sources are doing.
- **Testability:** unit-test `ingest()` against single-source scenarios; unit-test `ingest_batch()` against mixed-result scenarios (the >50% guard test goes here naturally).
- **Future-proofing:** HN/ProductHunt ingesters each have their own `ingest_batch` semantics (or share one); `ingest()` stays small.

**Refinement on the result struct** — include enough info that `ingest_batch` doesn't re-derive UA-block detection:

```python
@dataclass
class IngestResult:
    source_id: int
    items_captured: int
    status_codes: list[int]   # lets batch see 403 patterns across sources
    error_class: str | None
    latency_ms: int
```

The batch wrapper inspects `status_codes` across results to compute the UA-block fraction.

### Q3 — Separate `scripts/capture_reddit_fixture.py`, not integration-test-as-capturer

**Option (a) — separate script.** Symmetry with `scripts/capture_response_fixture.py` matters more than I'd usually weight; here it does because **capture is intentional**, not a side effect. Mixing assertion + capture into one test makes it harder to reason about *why* the fixture changed when something breaks.

Roles:

- **Integration test (`tests/integration/test_reddit_live.py`):** hit one real subreddit, assert ≥1 row inserted, schema contract holds against the live response, optionally print response shape for eyeballing. **Doesn't write fixtures.**
- **Capture script (`scripts/capture_reddit_fixture.py`):** hit specific endpoints, write `tests/fixtures/reddit/listing_*.json` with `_fixture_meta`. Run explicitly when needed (new fixture, refresh after contract failure).

Two artifacts, two clearly-named scripts, one mental model.

### Q4 — `# TODO verify` expectations: realistic, escalate at task end

Expecting 1-2 `# TODO verify` items at end of task 005 is realistic — Reddit's policy/docs landscape is genuinely unstable. The convention is *working* here: surfacing genuine uncertainty rather than burying it.

**Likely TODO-verify candidates:**

- **Unauth QPM ceiling.** Reddit doesn't publish this cleanly anymore. Best-effort sources: data-api-terms page or recent r/redditdev threads. If only community threads resolve, annotate as `# heuristic` rather than faking a `# verified` citation. Declaring values as judgment is better than fake authority.
- **UA format requirements.** Dev docs flakier than they used to be. Fall back to community-maintained scraper guides if the canonical page doesn't resolve.

**Escalation shape** if you land non-zero TODOs:

> Short orchestrator request: "these N constants have no authoritative URL; here's my best citation for each; confirm `# heuristic` is the right annotation."

For URL-less cases, I'll most likely confirm `# heuristic` rather than have you keep hunting. Don't burn hours on docs archaeology when the value is judgment anyway.

## Action items

Fold into task 005 implementation (no separate prep commit needed):

1. Custom validator with CLAUDE.md-pointing message for `reddit_username` (Q1).
2. `IngestResult` dataclass with `status_codes` field; `ingest()` returns it; `ingest_batch()` reads it (Q2).
3. `scripts/capture_reddit_fixture.py` separate from integration test (Q3).
4. Push commit `ef7cfb1` via whichever path access allows; switch to branch-per-task going forward.

## Next step

Implement task 005 on `feature/task-005-reddit-ingester`. Two-commit plan stands:

- `005a`: rate limiter + `sources.consecutive_failures` migration + `TokenBucket` unit tests.
- `005b`: Reddit ingester proper, with `IngestResult`/`ingest_batch` split, capture script, schema contract test, integration test (no fixture-write).

Expected end-state: 1-2 `# TODO verify` items remaining. Submit a short escalation request listing the candidates with best-known citations; I'll most likely re-annotate as `# heuristic` and task closes.

After task 005 closes: task 006 (HN ingester) is the first real test of whether `_rate_limit`'s source-agnostic shape holds up against a second consumer. Expect minor friction there — that's where the abstraction earns its keep or reveals its assumptions.

## Meta note

The branch-per-task shift is the right structural response to the classifier block, but it would have been the right move anyway. PRs become the natural place to attach orchestrator feedback files — they're already organized by task. Going forward, an orchestrator request can reference its parent PR by number, which makes the trail more navigable.
