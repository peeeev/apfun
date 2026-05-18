# Feedback 009 — final pre-task-005 calibrations + new contract-test convention

**Date:** 2026-05-18
**Request:** 009-pre-task-005-ready.md
**Outcome:** Four answers, including one genuine convention extension (contract tests for external schema shapes). Three action items before task 005 implementation.

## Answers

### Q1 — CI strategy

**Open a new task file now: `docs/tasks/023-github-actions-ci.md`.** Don't bundle CI with task 022 (digest email) — different concern, would get deferred or rushed.

Task 023 (Complexity: S):
- Wire `.github/workflows/check.yml` running `make check` on push/PR.
- Skip-condition for `test_fixture_is_real_capture` when running in CI without `APFUN_ANTHROPIC_API_KEY`:

```python
def test_fixture_is_real_capture():
    fixture = json.loads(FIXTURE_PATH.read_text())
    if "_meta_note" in fixture:
        if os.getenv("CI"):
            pytest.skip("synthetic fixture in CI; resolve locally before merge")
        pytest.fail("synthetic fixture — run scripts/capture_response_fixture.py")
```

Local: failure functions as a forcing function for the developer. CI: skipped so PRs aren't blocked.

- Also gate `make test-all` integration tests behind a manual `workflow_dispatch` trigger (don't burn API credits per PR).

**Sequencing:** between Phase E (Stages 3-5) and Phase F (output UI). PR review starts having teeth before the high-stakes pipeline tasks land, but not so early it imposes overhead during foundations.

### Q2 — Deleted/removed Reddit content — capture but tag

Your read is right. Ship it. Three reasons confirming:

1. **Deletion is signal.** A removed post about a SaaS pain still indicates the topic existed and was discussed — especially valuable when mod-removed in vertical subs (often correlates with hitting a nerve).
2. **Edit history.** Reddit sometimes restores content. Skipping on first sight loses the row permanently; capturing-and-tagging means it's there to re-process later.
3. **Storage is cheap.** Even at v1 volumes (~10k raw_signals/year), deletions are single-digit percent.

Shape:
```python
payload_json["is_deleted"] = True
payload_json["deletion_marker"] = "[deleted]" | "[removed]" | "<other>"
```

Stage 1 clustering (task 010) decides weighting — almost certainly down-weight `[deleted]` since `body == "[deleted]"` has no problem statement to cluster on. **Don't filter at ingest.**

### Q3 — Subreddit private/banned mid-stream + transient-vs-terminal refinement

Your base plan (catch 403/404, log, set `source.last_error`, continue) is correct.

**One refinement:** distinguish *transient* from *terminal* errors. A subreddit gone for a week isn't the same as a one-off 500.

```python
# Pattern: auto-disable source after N consecutive failures with terminal-looking
# status codes. Avoids hammering dead subreddits while not over-reacting to
# single bad days.
if consecutive_failures >= 3 and status in {403, 404}:
    source.is_active = False
    log.warning("disabled source %s after 3 consecutive %d", source.name, status)
```

Pick `consecutive_failures >= 3` as a starting value, `# heuristic 2026-05-18 — three strikes before disabling; balances responsiveness against transient errors`.

Track `consecutive_failures` on the source row — likely a new column `consecutive_failures INT DEFAULT 0` (alembic migration). Reset to 0 on any successful fetch.

### Q4 — External schema shapes — new convention: contract tests

**Real gap caught.** The verify-constants convention covers *values* but not *structural assumptions*. "Reddit's `data.children[].data.id` exists and is a string" is just as much an external dependency as a rate limit, and equally subject to silent breakage.

**Extend the convention with a third form: contract tests, not annotations.**

Schema shapes are best protected by tests that fail loudly when assumptions break, not by inline comments (which don't execute). Use the fixture you're already capturing:

```python
# tests/unit/test_reddit_schema_contract.py
def test_reddit_listing_response_shape():
    """Contract test: Reddit listing JSON must contain these fields.

    Captured: 2026-05-18 from r/programming via integration test.
    If this fails after a fixture refresh, Reddit changed their response shape —
    investigate before assuming the parsing code is broken.
    """
    fixture = load_fixture("reddit/listing_programming.json")
    assert fixture["kind"] == "Listing"
    assert "data" in fixture and "children" in fixture["data"]
    for child in fixture["data"]["children"][:3]:
        assert child["kind"] == "t3"
        d = child["data"]
        # Fields the ingester depends on:
        for field in ["id", "subreddit", "title", "selftext", "score",
                      "num_comments", "created_utc", "permalink", "url"]:
            assert field in d, f"missing field {field} in Reddit response"
```

Qualitatively different from the Anthropic tripwire test:
- Anthropic tripwire validates against a Pydantic model the SDK ships → catches SDK breakage.
- Reddit schema contract validates against fields *your code* assumes exist → catches third-party API breakage.

Both belong in the unit suite (fast, deterministic, against fixtures). Refresh fixtures opportunistically.

**Add to CLAUDE.md → Conventions, as a sibling rule to verify-constants:**

> **Contract tests for external schemas.** When parsing third-party API responses, assert the fields your code depends on in a `test_<source>_schema_contract.py` test against a captured fixture. If the test fails after a fixture refresh, the third party changed their response shape — investigate before adjusting the parser.

This generalizes: every external integration gets a contract test. Task 007 (ProductHunt GraphQL) and task 009 (G2/Capterra) inherit the pattern naturally.

## Action items

In the prep commit before task 005 implementation:

1. Open `docs/tasks/023-github-actions-ci.md` per Q1. Spec only — implementation later.
2. Extend CLAUDE.md → Conventions with the contract-test rule per Q4.
3. Bake the consecutive-failures auto-disable into the task 005 plan, including the `consecutive_failures` column migration. (Q3 refinement)

Q2 needs no action — ship the capture-but-tag pattern as you planned.

## Meta note on Q4

This question demonstrates the convention is working — you found a gap by genuinely thinking through what the existing convention protects against, then noticed schema shapes are a different category of risk needing a different mechanism. That's how disciplines grow in healthy directions instead of ossifying.

The full shape now:
- **Values** from external sources → `# verified` or `# heuristic` annotations.
- **Schema shapes** from external APIs → contract tests against captured fixtures.
- **SDK-shipped models** (e.g., Anthropic's `Message`) → tripwire tests with `model_validate`.

Lock this triple in now while there's one ingester to apply it to, before five exist with inconsistent treatment.

## Next step

Apply action items 1-3 in a prep commit. Then proceed to task 005 (Reddit ingester) implementation with all the design choices baked in: per-source rate limiter, fail-loud UA, content-hash with body[:500], full payload_json, capture-but-tag deletions, three-strikes auto-disable, both `# verified` and `# heuristic` constants, schema contract test against captured fixture.
