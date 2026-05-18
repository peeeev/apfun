# Request 008: feedback-007 applied — synthetic-fixture forcing function armed

**Date:** 2026-05-18

**Context**: Feedback 007 received and applied at commit `5df5c2f`. Three small calibrations landed. **Important state change**: `make check` is now intentionally **red** on a single test (`test_fixture_is_real_capture`) — the deliberate forcing function from feedback-007 Q2. All other tests (29) pass. Ready to start task 005, with one open question about how/when the fixture capture happens.

**What I just did**:

- **`_meta_note` stripped before validation** (feedback 007 Q4). `tests/unit/test_anthropic_response_shape.py` now pops `_meta_note` before `Message.model_validate`. The meta-field is metadata about the fixture, not data the SDK ever sees — making them orthogonal eliminates the future-SDK-`extra="forbid"` fragility risk.
- **`test_fixture_is_real_capture` added** (feedback 007 Q2). Fails until someone runs `scripts/capture_response_fixture.py` with `APFUN_ANTHROPIC_API_KEY` set and commits the regenerated fixture. The error message links to the exact resolution command. No `xfail` per the feedback ("a failing test is a clearer prompt than xfail that becomes background noise").
- **INDEX row 006** (feedback 007 Q1). Summary now spells out the 021-vs-022 redirect explicitly: "budget health panel noted in task **021** (feedback referenced 022; admin observability lives in 021)" — searchable later.

**What I would do next without intervention**: Task 005 (Reddit ingester). I have the tactical heads-up from feedback 007 baked into my plan:

- Sync `httpx.Client` with token-bucket-style rate limiter (~3-4 req/s sustained with jitter; `# verified` annotated against Reddit's community-practice notes)
- UA string `apfun-funnel:v0.1 (by /u/<reddit-username>)` — `<reddit-username>` parameterized via env so the placeholder doesn't ship; UA format annotated with the Reddit API docs URL
- content_hash on `(subreddit, external_id, title, body[:500])` — captures dedup-after-edit
- `raw_signals.payload_json` stores the full Reddit response, not a normalized subset (storage cheap, schema flexibility valuable)
- All Reddit external constants (QPM ceiling, UA format, pagination shape) carry `# verified YYYY-MM-DD <url>` annotations or `# TODO verify` markers if I can't resolve a value in-PR

**Specific questions or risks**:

- **`make check` red state — resolution path**. The intentional forcing-function test fails because I can't run the capture script from this dev container (no `APFUN_ANTHROPIC_API_KEY` in env, no real API access). The clean path is one of:
  - **(a)** You run `APFUN_ANTHROPIC_API_KEY=... uv run python scripts/capture_response_fixture.py` on the host (or in this container via `!` shell prefix) before I start task 005; the regenerated fixture gets committed and CI goes green.
  - **(b)** I proceed with task 005 against a red `make check`; the gate stays red until (a) happens, but task 005 progress is unblocked because the failing test is unrelated.
  - **(c)** You paste the captured fixture content here and I commit it directly.
  - Lean (a) — clean state before task 005 starts. If that's slow/inconvenient, (b) is acceptable per the feedback's design intent (failing test as visible prompt).
- **Reddit-username for UA string**. Reddit's UA requires `(by /u/<username>)` — non-conformant UAs get silently blocked. I don't have your Reddit handle. Plan: pull from `APFUN_REDDIT_USERNAME` env var (empty default), with a startup check that warns when empty and falls back to `(by /u/apfun-funnel)` so the app doesn't crash. Annotate the UA-format requirement against Reddit's API docs URL. Confirm or override.
- **Rate limiter scope**. I plan to put the token-bucket rate limiter as a small module inside `apfun/sourcing/`, scoped per-source (Reddit gets its own bucket). When task 006 (HN ingester) and task 007 (ProductHunt) land, each gets a separate bucket — no global throttle. Confirm that scope, or want a shared "external HTTP" bucket?
- **`# verified` for community-practice constants**. The "~3-4 req/sec sustained" rate target comes from community guidance, not Reddit's published docs (the docs only state the QPM ceiling). For external constants that are *consensus* rather than *published*, what's the right annotation? `# verified 2026-05-18 community-consensus` feels weak. Suggest a stronger form, or accept that some external constants are heuristic and annotate them as such?

**Relevant files/diffs**:

- commit `5df5c2f` (feedback-007 application)
- `tests/unit/test_anthropic_response_shape.py` — strip `_meta_note`, new failing test
- `docs/orchestrator/INDEX.md` — row 006 redirect note
