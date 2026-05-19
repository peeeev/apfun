# Feedback 013 — task 007 prep + abstraction timing

**Date:** 2026-05-19
**Request:** 013-task-007-prep-and-abstraction-question.md
**Outcome:** Confirms all four leans. Task 007 proceeds against the current duplication; unification deferred to a dedicated refactor PR after 007 merges.

## Answers

### Q1 — Unify-after-007 confirmed (your lean is correct)

The point of "wait for the third call site" wasn't "have three samples then average" — it was **don't let the first two call sites dictate the abstraction shape**. ProductHunt is genuinely likely to be different in ways that matter:

- **GraphQL paging** is cursor-based; Reddit's `after` and HN's Algolia paging are both REST-style.
- **Token-auth + 401 → re-auth** is a new failure mode neither current ingester handles. Slotting it into a `_base.py` designed without it forces ugly hooks.
- **Rate-limit headers** — ProductHunt's GraphQL likely returns rate-limit info in response headers (`X-RateLimit-Remaining` style). Reddit and HN don't surface anything comparable. Whether `TokenBucket` should adapt at runtime is a real design question — but only when there's a concrete reason in code.

Unifying now means every one of these differences becomes "how do I shim ProductHunt's shape into the abstraction I just created" — textbook premature abstraction.

**After 007 ships:** dedicated commit/PR for unification. **Not** folded into 007's PR. Keep the unification diff reviewable on its own merits without ingester-functionality noise. Likely shape: `refactor: extract shared sourcing skeleton (no behavior change)`.

**Discipline going forward:** don't bundle refactoring with feature work.

**On "messy files":** three files at ~300-400 lines with well-bounded, self-contained duplication isn't messy — it's *redundant*. Mess is *inconsistent* duplication (different file structure, naming, error paths). Yours is consistent duplication, which is the easy kind to refactor cleanly when the moment comes.

### Q2 — GraphQL with Client-only token: confirmed

Your justifications all stand. Schema versioning is the additional point: GraphQL APIs evolve via deprecations + additions (mostly), giving you a multi-month deprecation window. Compared to scraping where any HTML rejig breaks you, that's real money in fewer fire drills.

**Token scope guidance:** ProductHunt developer tokens come in two flavors — *Client-only* (read-only) and *User-context* (acts as a user). **Use Client-only.** Long-lived (no rotation cycle), sufficient for read-only ingestion, less sensitive if leaked. Note this in the task spec.

**Apply the Anthropic pattern for `APFUN_PRODUCTHUNT_TOKEN`:** empty default, fail-loud at first API call with a clear message. ProductHunt returns 401 with a clean error if the token is missing/invalid — not silent-degradation territory.

### Q3 — Auth-secret discipline as a CLAUDE.md convention: yes, with refinement

Document it. Your proposed text is clean; one refinement to make the convention self-evaluable:

> **Auth secret discipline.** External-service secrets are env vars under the `APFUN_` prefix. The fail-loud point depends on *how the third party fails when the secret is missing*:
>
> - **Silent degradation** (returns wrong-account results, phantom-empty data, or otherwise plausible-looking-but-wrong output) → fail at `Settings()` construction with a CLAUDE.md-pointing message. *Example:* Reddit username (UA-blocked → empty results that look like "no new content").
> - **Loud failure** (returns a clear authentication error like 401/403 with a meaningful message) → empty default; fail at the call site with a clear message. *Examples:* Anthropic API key, ProductHunt token.
>
> When in doubt about which category a service falls into, test it: configure the service intentionally wrong and observe whether it errors or silently returns garbage. The empirical answer governs.

The "when in doubt, test it" line is the operational value — without it, the convention is just "look at the existing examples and guess."

Add this in **task 007's prep commit**, not as a separate refactor — it's a natural prep step for the integration that exercises the loud-failure case.

### Q4 — Test-side duplication: leave alone, confirmed

Test code's primary virtue is *being obvious when you're debugging a flake at 11pm*. Shared test helpers introduce indirection at exactly the moment you don't want it. Duplication is real but cheap; indirection is invisible most of the time and ruinous occasionally.

**Threshold for revisiting:** if you find yourself copying the same 30+ line block across more than 3 test files, consider extracting — but to a clearly-named helper (`tests/_helpers/mock_clients.py`) documented as "patterns that genuinely don't differ across consumers." High bar. Stay below it for now.

## Action items

For task 007:

1. Start `feature/task-007-producthunt-ingester` against the current duplicated shape — no unification.
2. GraphQL + Client-only token. `APFUN_PRODUCTHUNT_TOKEN` empty-default, fail-loud at first call. Note token-scope choice in the task spec.
3. Add the auth-secret discipline convention to `CLAUDE.md` → Conventions in task 007's prep commit, with the "when in doubt, test it" line included.

After task 007 merges:

4. Open `feature/refactor-sourcing-base` (no task number, no functional change) as a behavior-preserving refactor PR. Likely small if the three implementations are as parallel as Q1 suggests.

## Next step

Task 007 implementation. Three things to flag if they come up:

- **ProductHunt rate limits** aren't well-published. Another `# heuristic` likely — start conservative (1-2 req/sec sustained) and tune later.
- **Topic surface vs leaderboard surface:** newer launches vs curated. Pick deliberately based on what shows the *negative space* signal we care about (what's hot → by inference what's *missing*). Lean toward both surfaces with different filtering, but make the call in the task spec.
- **Vote-count filter:** ProductHunt has many low-signal launches. A vote/upvote threshold (analog to HN's points filter) probably makes sense. Default conservative; configurable per source.

## Meta note

A small observation about the project's trajectory: the orchestrator turns are getting structurally similar — request lands, three or four well-formed questions, brief refinements on the proposal that came in, ship. That's a sign the *upstream* discipline is working well. Specs are arriving fully derived from prior decisions; orchestrator turns are catching genuine ambiguities rather than papering over insufficiently considered designs.

**If a turn ever feels like it's just rubber-stamping** ("agreed, agreed, agreed"), that's a signal to *not* run the turn. Save the cycles. Disagreement and refinement is what makes the loop valuable.

The current cadence is healthy. Don't break it, but also don't preserve it ritually if a task genuinely doesn't have an architectural question worth surfacing.
