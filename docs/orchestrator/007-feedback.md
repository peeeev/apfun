# Feedback 007 — pre-task-005 calibrations + Reddit ingester heads-up

**Date:** 2026-05-18
**Request:** 007-pre-task-005-tidy.md
**Outcome:** All four questions answered. Two small prep items before task 005 starts. Tactical heads-up on Reddit ingester design (non-blocking).

## Answers to the four questions

### Q1 — Task placement (021 vs 022)

**021 is correct.** Budget health panel is administrative observability → fits the admin UI for sources/projects. Task 022 (digest email) is end-user output, different concern. The one-line redirect note in your application is the right discipline.

Note the redirect in INDEX.md row 006 summary so future-you can find it.

### Q2 — Synthetic-fixture CI detection

**Add the guard.** Cost is one line; payoff is real protection against shipping the synthetic fixture indefinitely.

```python
def test_fixture_is_real_capture():
    """Guard against shipping the synthetic fixture indefinitely."""
    fixture = json.loads(FIXTURE_PATH.read_text())
    assert "_meta_note" not in fixture, (
        "Fixture is still synthetic. Run scripts/capture_response_fixture.py "
        "with APFUN_ANTHROPIC_API_KEY set to capture a real response."
    )
```

**Let it fail rather than `xfail`.** A failing test is a clearer prompt than an xfail that becomes background noise. Run the capture script during your next `make test-all` and the failure resolves itself.

### Q3 — Reddit auth: stay unauthenticated for v1

**Public JSON, no OAuth.** Three reasons:

1. **Volume profile fits.** Sourcing runs every 6 hours over ~20 subreddits = ~80 listing calls/cycle, ~320/day. The unauthenticated ceiling (10 QPM ≈ 600/hr) covers this comfortably. OAuth's 100 QPM upgrade only matters at orders of magnitude more traffic.
2. **Reddit's auth landscape is unstable.** Their 2023 policy changes were aggressive and ongoing — OAuth app approvals spotty, ToS ambiguous about AI/automation use cases. Adding that operational complexity now buys nothing for v1 volume.
3. **YAGNI on data-source auth.** When you hit a real rate-limit wall (or Reddit blocks the unauthenticated path), you'll know — *then* OAuth becomes worth the setup.

**Failure-mode plan:** when Stage 1 starts logging Reddit 429s with any frequency, open an orchestrator request. That's the trigger for OAuth migration, not a pre-emptive build-out.

### Q4 — `_meta_note` SDK fragility

**Strip it in the test.** One line, eliminates the future-fragility risk forever:

```python
fixture_dict = json.loads(FIXTURE_PATH.read_text())
fixture_dict.pop("_meta_note", None)
Message.model_validate(fixture_dict)
```

The meta-field is metadata *about* the fixture, not data the SDK ever sees — making them orthogonal is the right model. Real-capture replacement closes the risk permanently; until then, stripping is the cheap belt-and-suspenders move.

## Tactical heads-up — Reddit ingester design

Non-blocking. These are things worth surfacing as you build task 005, beyond rate-limit annotation:

### Polite rate limiting in code

Reddit's documented QPM is a *ceiling*, not a budget. Hammering 600/hr triggers throttling even when "under." Target ~3-4 requests/second sustained with jitter. Wrap `httpx.Client` calls in a small rate limiter (token bucket or sleep-after-call). Annotate the chosen rate with `# verified` against community practice notes.

### User-Agent string format

Reddit explicitly requires `<platform>:<app-id>:<version> (by /u/<reddit-username>)`. Many scrapers fail this format silently and get blocked without explanation. The username field is the one that catches people — Reddit wants to know who you are.

Example format that works:
```
apfun-funnel:v0.1 (by /u/<your-username>)
```

Annotate this with a link to Reddit's UA policy doc.

### Content-hash dedup field choice

Don't hash post bodies alone — Reddit edits republish with the same external IDs. Hash on `(subreddit, external_id, title, body[:500])` to catch dedup-after-edit. Body slicing protects against minor edits triggering spurious new rows.

If you want to capture edit history later, that's a separate column (`edited_at`, `revision_count`) — not solved by dedup choice.

### `raw_signals.payload_json` shape

Decide upfront: full Reddit response, or normalized subset?

**Recommend full-response.** Storage is cheap; schema flexibility for later analyses is valuable; migration to normalized later is straightforward if you decide you need it. Normalize at read time for analytical queries via SQLAlchemy/pydantic; keep the source-of-truth fat.

Note this in the task's CLAUDE.md addition or model docstring.

## Action items

Before task 005:

1. Add `test_fixture_is_real_capture` (failing, no xfail).
2. Strip `_meta_note` in the tripwire test's validation step.
3. Update INDEX.md row 006 summary with the 021-vs-022 redirect note.

During task 005, surface any of the tactical items above that aren't already in your plan and we can iterate at that point. Otherwise they're guidance to apply directly.

## Next step

Proceed to task 005 (Reddit ingester). First non-LLM exercise of the verify-constants convention. External constants to annotate or `# TODO verify`:

- Reddit unauthenticated QPM (verify current value at `support.reddithelp.com` or `redditinc.com/policies/data-api-terms`)
- User-Agent format requirements (Reddit API docs)
- JSON endpoint pagination shape (25-item default, `after` cursor format)
- Per-subreddit endpoint quirks if any surface
