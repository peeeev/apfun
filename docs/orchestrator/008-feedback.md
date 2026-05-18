# Feedback 008 — pre-task-005 final calibrations

**Date:** 2026-05-18
**Request:** 008-synthetic-fixture-guard.md
**Outcome:** Four substantive answers. One genuine convention improvement (new `# heuristic` annotation form). Three action items before task 005 implementation. Proceed to task 005 with the heads-up items folded in.

## Answers

### Q1 — Red `make check` state

**Option (b) — proceed against the red gate.** The failing test is doing its job: it's a visible, actionable prompt. The prompt doesn't have to gate other work.

Your task 005 tests will be unrelated. Run subsets (`pytest tests/unit/test_reddit*.py`) to verify your task's green status without the noise.

When you naturally cycle through `make test-all` during task 005 (which you'll want to do at least once to verify the LLM wrapper still works end-to-end against real API), capture the fixture in the same session and commit. Two birds.

**Add a workflow note to CLAUDE.md → Tests:**

> If `make check` fails only on `test_fixture_is_real_capture`, that's the intentional synthetic-fixture forcing function. Resolve by running `APFUN_ANTHROPIC_API_KEY=... uv run python scripts/capture_response_fixture.py` and committing the regenerated `tests/fixtures/opus_4_7_with_cache.json`. Until then, `make check` red on this single test is expected and unblocks other work.

### Q2 — Reddit username — fail loudly, not warn

Your plan is mostly right with one important tweak. **Empty username must fail at startup, not warn.**

```python
# apfun/sourcing/reddit.py
REDDIT_USERNAME = settings.reddit_username  # APFUN_REDDIT_USERNAME

if not REDDIT_USERNAME:
    raise RuntimeError(
        "APFUN_REDDIT_USERNAME is required. Reddit silently blocks "
        "non-conformant User-Agents; an empty username produces "
        "phantom-empty results, not errors. Set the env var to your "
        "Reddit handle and restart."
    )

USER_AGENT = f"apfun-funnel:v0.1 (by /u/{REDDIT_USERNAME})"
```

**Why fail, not warn:**

A misconfigured UA → Reddit silently blocks the request → ingester gets zero results back as "successful." This is *worse* than a crash because the bug presents as "Reddit sourcing isn't finding anything new" — which looks like a content problem, not a configuration problem. Hours of debugging the wrong thing.

Hard fail at startup forces the operator to fix it before any data flows. Validate in `apfun/config.py` if you prefer that location — either's fine, just don't let a missing username slide into runtime.

Username lives in `.env` (already gitignored); `.env.example` gets a documented placeholder line. Don't bake the username into code.

### Q3 — Rate limiter scope — per-source, with source-agnostic implementation

**Your plan (per-source buckets) is correct.** Three reasons:

1. **Different rate-limit contracts per source.** Reddit's 10 QPM unauth vs. HN's "no published limit, be nice" vs. ProductHunt's GraphQL throttling are different ceilings. A global bucket would either be too tight for the loose ones or too loose for the strict ones.
2. **Failure isolation.** Reddit 429s shouldn't slow down HN scraping. Per-source buckets keep one source's outage from cascading.
3. **Visibility.** "Why is sourcing slow?" is easier to answer with `rate_limiter.reddit.tokens_available` than with one mystery counter.

**Refinement:** the rate limiter module itself should be source-agnostic. Put `TokenBucket` (or similar) in `apfun/sourcing/_rate_limit.py`; each ingester instantiates its own bucket with source-specific params. Don't put Reddit-specific values in the rate limiter module — that couples it to Reddit and makes HN/PH ingesters either duplicate the code or fight the abstraction.

```python
# apfun/sourcing/_rate_limit.py
class TokenBucket:
    def __init__(self, rate_per_sec: float, burst: int): ...
    def acquire(self) -> None: ...   # blocks if needed

# apfun/sourcing/reddit.py
_BUCKET = TokenBucket(rate_per_sec=3.5, burst=5)
```

### Q4 — Consensus-vs-published constants — new `# heuristic` form

Genuine improvement to the convention. Add a second annotation form:

```python
# heuristic 2026-05-18 — community consensus: aim ~3-4 req/sec sustained
# against Reddit's 10 QPM ceiling, leaving headroom for spikes.
# Source(s): <link to relevant thread/wiki, or "see CLAUDE.md note">
_REDDIT_RATE_PER_SEC = 3.5
```

**Why `# heuristic` (different keyword from `# verified`):**

- `# verified` implies a citable authority you could point to.
- `# heuristic` is honest about judgment-derived values.
- Different keyword is greppable: `grep -r '# heuristic'` lets you audit "what values in this codebase are based on judgment vs authority?" — useful when something starts misbehaving.

**Update CLAUDE.md convention text:**

> **External constants only.** Values from third-party sources outside this repository must carry one of:
>
> - `# verified YYYY-MM-DD <source-url>` — when an authoritative source exists (pricing pages, API docs, RFCs).
> - `# heuristic YYYY-MM-DD — <rationale and any reference>` — when the value is judgment-derived from incomplete information (community consensus, observed-good-behavior thresholds).
>
> Internal project decisions (`host="0.0.0.0"`, enum values, computed bounds) don't require annotation.

## Aside — task 005 design heads-up

Non-blocking. Fold in as you build:

### Structured per-call logging

Reddit ingester logs per-listing-call outcome at INFO with a structured shape: `{subreddit, status_code, items_returned, latency_ms, error_class?}`. The sources-health UI (task 021) will read this; even before that, the logs are what you'll grep when something stops working.

### Retry policy — accept duplication with LLM wrapper for now

Token bucket handles "don't exceed rate"; doesn't handle "Reddit returned 503, try again." Separate concern. Three retries with exponential backoff is the standard pattern.

The LLM wrapper already established a retry shape. **Don't extract a shared helper yet.** Premature abstraction is worse than two retry implementations until you have a third call site to triangulate the right shape. When task 007 (ProductHunt) lands, the third example will tell you what the abstraction should look like.

### Test fixtures

Real Reddit JSON is large and inconveniently shaped. Capture one or two via the integration test and commit to `tests/fixtures/reddit/`. Mock `httpx.Client` against these for unit tests. Real-API integration tests stay gated behind `make test-all`.

## Action items

Before task 005 implementation, in one prep commit:

1. Update CLAUDE.md convention text to recognize both `# verified` and `# heuristic` (Q4).
2. Add the workflow note to CLAUDE.md → Tests for the red-state resolution (Q1).

Then proceed to task 005 with the Q2/Q3 design choices baked in and the aside items folded in inline as appropriate.

## Meta note

Q3 is the kind of question that's worth ~10x more before three ingesters exist than after. Surfacing it now is the right discipline. Same for Q4 — a convention extension is much cheaper to make at file-3 than file-30.
