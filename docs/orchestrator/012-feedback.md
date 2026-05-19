# Feedback 012 — TODO-verify resolution + task 005 PR shape

**Date:** 2026-05-19
**Request:** 012-task-005-todo-verify-escalation.md
**Outcome:** Both items re-annotated as `# heuristic` (confirmed). PR-body shape sketched. Task 006 heads-up included.

## Answers

### Q1 — `# heuristic` re-annotation: confirmed for both

Re-annotate, drop the `# TODO verify` lines, close the loop.

**Why this resolution is correct:**

- **Unauth QPM ceiling.** Reddit has effectively stopped publishing this for the unauth path. r/redditdev community consensus is the most authoritative source available. Forcing `# TODO verify` to stay creates a permanent unresolvable item — exactly the failure mode the convention is meant to avoid. `# heuristic` honestly describes the situation.
- **UA format.** Same shape. The "silently degrades non-conformant UAs" claim is empirically true regardless of which doc page currently documents it. PRAW's UA format is the de-facto standard used by every major Reddit client; that's a stronger citation than a Reddit doc URL that might 404 next month.

This resolution validates the convention: `# heuristic` is the escape valve for "judgment-derived from incomplete information," and there will always be values that fit it. `# TODO verify` is for *temporary* unverification (URL not checked yet, etc.), not permanent.

### Q2 — Citation text: verbal, not URL

**Verbal description.** URL-as-citation in code rots faster than the value itself — you just demonstrated this twice. The annotation's job is to record *the kind of authority* behind the value, not provide a hyperlink. A future reader who wants to verify can search "PRAW user-agent format" themselves; they don't need a specific URL that 404s.

Cleaner final shape:

```python
# heuristic 2026-05-19 — Reddit unauth public-JSON ceiling is community-
# reported as ~10 QPM per IP; no authoritative current page. r/redditdev
# threads are the best signal. Our 3.5 req/s sits well under any
# reasonable ceiling; this constant is for sanity-checking, not for
# enforcement.
_REDDIT_UNAUTH_QPM_CEILING = 10
```

```python
# heuristic 2026-05-19 — Reddit silently degrades non-conformant UAs.
# Format `<platform>:<app>:<version> (by /u/<handle>)` is the community
# convention used by PRAW, snoowrap, and similar libraries. Reddit's own
# API-rules page once spelled this out but has been reorganized away.
_USER_AGENT = f"apfun-funnel:v0.1 (by /u/{settings.reddit_username})"
```

Comments stand on their own — describe *why* the value was chosen and what evidence supports it, without depending on an external link surviving.

### Q3 — PR shape: freestyle, no template yet

No template needed at this stage. Suggested body shape:

```markdown
Implements task 005 per docs/tasks/005-reddit-ingester.md.

## What landed
- (brief actual changes)

## Decisions traced to orchestrator feedback
- feedback 007 → polite rate limiting, UA format, content-hash, payload_json shape
- feedback 008 → per-source bucket scope, fail-loud UA, `# heuristic` annotation form
- feedback 009 → UA-block batch guard, contract test, capture-but-tag deletions
- feedback 010 → TERMINAL_STATUSES incl. 410, fixture meta header, task-scoped TODOs
- feedback 011 → custom validator with CLAUDE.md pointer, IngestResult + batch separation, separate capture script
- feedback 012 → final `# heuristic` re-annotation for unresolved URLs

## Two heuristic constants (no authoritative URL exists)
- `_REDDIT_UNAUTH_QPM_CEILING` (10) — community-reported, sanity-check only
- `_USER_AGENT` format — community convention (PRAW etc.)
- Both annotated and explained inline.

## Tests
- 54 unit tests, 0 pyright errors, ruff clean
- Schema contract test against captured fixture
- Integration test gated behind `make test-all`

## What's next
Task 006 (HN ingester) — first real exercise of `_rate_limit`'s source-agnostic shape.
```

Body gives the reviewer a fast read of *what* and *why-this-way*. Cross-references to feedback files create a navigable trail through the project's decision history. Formalize into a template only when you find yourself copy-pasting it across tasks.

## Action items

1. Strip both `# TODO verify` lines; replace with cleaner `# heuristic`-only annotations.
2. Verify `grep -r '# TODO verify' apfun/` returns zero before opening the PR.
3. Open the PR with the freestyle body shape.
4. Once merged, proceed to task 006.

## Next step + task 006 heads-up

Task 006 (HN ingester) — first real test of `_rate_limit.TokenBucket`'s source-agnostic shape against a second consumer.

**Expected friction points:**

- **API choice:** HN has two surfaces — Firebase REST and Algolia search. Algolia is the better fit for "Ask HN" thread mining (better search semantics, supports time-bounded queries). Pick one and stick to it across the ingester.
- **Rate limits:** HN doesn't publish them cleanly either. Another `# heuristic` likely — Algolia's terms suggest "be reasonable"; community practice is 1-2 req/sec sustained. Same shape as the Reddit annotation.
- **No fail-loud UA needed:** HN doesn't UA-block in the same way Reddit does. The fail-loud pattern from feedback 011 was Reddit-specific; HN can use a static UA constant.
- **Contract test against fixture:** same pattern as Reddit. Capture script + schema contract test + integration test.

**If `TokenBucket` needs to grow** (different burst semantics, different blocking mode, async support), that's the signal to escalate the abstraction shape rather than silently extending it. Open a request before generalizing — second consumer is the right moment to discuss what the abstraction actually needs to support.

## Meta note

Task 005 went well end-to-end: 5 orchestrator turns from spec to merge-ready, no design churn, all surprises caught at the spec stage rather than mid-implementation. The two-`# heuristic` outcome is exactly the convention working as designed — surfacing genuine uncertainty rather than burying it under fake citations.

The branch-per-task cadence proved its value already; this is the first task with a PR-shaped artifact for review.
