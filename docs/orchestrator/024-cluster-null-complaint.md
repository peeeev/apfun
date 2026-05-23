# Request 024: task 010-fix-1 — cluster robust to non-clusterable signals

**Date:** 2026-05-23

**Context.** A real-data cluster run failed today against the 86 signal_text rows accumulated since runbook 001 (HN data + scheduler-driven Reddit ingest from the past 24h). The first Haiku `dedup` call returned a structured response with `core_complaint: null` for at least one signal, triggering a Pydantic validation error against the `SignalCoreComplaint` schema. The wrapper's retry loop interpreted this as `JSONParseError`, retried 3 times against a deterministic Haiku output, and finally raised — aborting the entire cluster run.

**Concrete traceback (operator's REPL):**

```
NormalizeResult(processed=86, inserted=75, updated=0, skipped=11, latency_ms=24)

cluster.failed
...
apfun.llm.client.JSONParseError: 'dedup': response did not match schema SignalCoreComplaint:
  1 validation error for SignalCoreComplaint
  core_complaint
    Input should be a valid string [type=string_type, input_value=None, input_type=NoneType]
```

This is a real-data-vs-schema-assumption bug, the same category as the three caught in runbook 001 (SAVEPOINT-rollback, JSON-fence, Opus-thinking-API). The lesson "synthetic tests don't catch surface-changing bugs" from feedback 018 holds at the per-stage level — Stage 1's clustering has now had its empirical-validation moment.

**The substantive design problem.** The current `SignalCoreComplaint` schema requires `core_complaint: str` (non-null). But real-world signal text isn't uniformly complaint-shaped:

- Reddit posts where the body is `[deleted]` or `[removed]` but the *title* didn't get marked `is_low_signal=True` during normalization
- HN posts in `Show HN`-style threads that are announcements, not complaints
- Posts whose actual text doesn't describe a problem (e.g., success stories, off-topic discussion, meta-content)

Haiku is being honest by returning `null` when no complaint is discernible. The previous behavior (forcing it to return a string) would have produced low-quality invented complaints that pollute downstream clusters. **The right fix is to accept Haiku's null answer and skip those signals, not to coerce Haiku into inventing complaints.**

## Goal

Make the Stage 1 cluster pipeline robust to "this signal doesn't contain a clusterable complaint." Specifically:

1. `SignalCoreComplaint.core_complaint` accepts `None`.
2. Haiku's `dedup_signal.j2` prompt explicitly permits null when no complaint exists.
3. When Haiku returns null for a signal, the pipeline marks that signal so it isn't re-Haiku'd on every future cluster run (avoids wasted LLM cost).
4. A batch with some null-complaint signals completes successfully against the remaining valid ones — no aborts.

## Scope

**In scope:**

- `apfun/models/cluster.py` (or wherever `SignalCoreComplaint` lives) — type `core_complaint: str | None`.
- `apfun/llm/prompts/dedup_signal.j2` — add an explicit instruction allowing the model to emit `null` for `core_complaint` when no problem statement is present in the signal. Same for `vertical` and `keywords` (likely null in the same scenarios). Keep the strict-JSON output requirement.
- `apfun/pipeline/cluster.py` `_haiku_prepass()` (or equivalent) — handle null results. On null, update the corresponding `signal_text` row to indicate "not clusterable" (see Q1 below for *how* to indicate). Filter null-complaint enriched signals out before bucketing.
- Update unit tests for the schema accepting None.
- Update unit tests for the prepass — assert null Haiku responses don't crash, and that the signal is marked appropriately for future skip.
- One updated/new fixture in `tests/fixtures/` showing a null-complaint response (capture or hand-craft; the synthetic-fixture convention from feedback 018 Q1 applies).

**Out of scope:**

- Changing the Opus `cluster.j2` prompt or schema. Opus operates on already-enriched, non-null buckets; its contract doesn't change.
- Re-running the entire 86-row cluster — that's an operator action post-merge, not part of this PR.
- Hard cost limits, budget gates, or any per-call abort logic. Existing retry/cap infrastructure stays as-is.

## Q1 — How to mark signals as non-clusterable (open design)

Three options, ordered by my preference:

### (a) — Reuse `is_low_signal`, broaden semantics

Set `signal_text.is_low_signal = True` when Haiku returns null. The cluster pipeline already skips low-signal rows (per feedback 016 Q6). Reuses existing column.

**Pros:** No schema migration. Minimal code change. Same skip-on-cluster behavior already works.

**Cons:** `is_low_signal` was specced (in 010a) as "structural" (set during normalization, e.g., `[deleted]`). This adds a "judgment" meaning — "Haiku judged this not-clusterable." Slight conceptual smearing.

To mitigate: document the broadening explicitly in CLAUDE.md and in `signal_text` model docstring. Future code reading "is_low_signal" should know it covers both structural and Haiku-judgment cases.

### (b) — New column `signal_text.is_clusterable: bool`

Add a separate column. Default `True`. Set `False` when Haiku returns null. Cluster pipeline filters on `is_clusterable = True AND is_low_signal = False`.

**Pros:** Clean separation. Each column has one clear meaning.

**Cons:** Migration overhead. Two columns to remember to filter on in every downstream query.

### (c) — Don't persist anything; let cluster re-Haiku each run

Just skip null-complaint signals in-memory each cluster invocation. Pay the Haiku cost every run.

**Pros:** No DB changes.

**Cons:** Wasted Haiku calls (~$0.001/signal, but compounds at scale). Defeats the "cluster permanently skips already-clustered signals" pattern from feedback 016 Q8 — null-complaint signals would be perpetually "unclustered" and re-Haiku'd.

**My lean: (a).** The conceptual smearing is real but small, and the alternative requires migration overhead that doesn't pay off proportionally. Document the broadened semantics; move on. Open to (b) if the implementer thinks the cleanliness is worth it.

## Implementation shape

Suggested approach for `_haiku_prepass()` (rough sketch — adapt to actual codebase shape):

```python
def _haiku_prepass(llm_client, signals):
    enriched = []
    for signal in signals:
        try:
            result = llm_client.mechanic_json(
                "dedup", ..., schema=SignalCoreComplaint,
            )
        except SomeOtherError:
            # genuine LLM errors stay loud
            raise

        if result.core_complaint is None:
            _mark_non_clusterable(signal.id)  # per Q1 decision
            continue

        enriched.append((signal, result))
    return enriched


def _mark_non_clusterable(signal_text_id):
    with SessionLocal() as s:
        s.execute(
            update(SignalText)
            .where(SignalText.id == signal_text_id)
            .values(is_low_signal=True)  # if Q1 (a)
        )
        s.commit()
```

Done inline per-signal. Don't batch the marking — keeping it per-signal means a partial-failure mid-batch still records the work done up to that point.

## Tests

- Unit: `SignalCoreComplaint(core_complaint=None, vertical=None, keywords=None)` validates.
- Unit: `_haiku_prepass` against a stub LLM client returning null `core_complaint` for one of N signals — assert the null signal is marked appropriately and not in the enriched output; assert other signals proceed normally.
- Unit: re-running `_haiku_prepass` against a previously-null signal skips it (it's now `is_low_signal=True`).
- Fixture: capture or hand-craft a Haiku response with `core_complaint: null` for the schema-validation test.
- Integration test: gated, but if real Haiku is called, the test should accept null-complaint responses as valid pipeline state, not as failures.

## Documentation updates (same PR)

1. **`CLAUDE.md → Lessons Learned`** — new entry, dated 2026-05-23:

   > **Real-world signal text isn't uniformly complaint-shaped.** Stage 1's Haiku dedup pass returned `core_complaint: null` for signals that didn't contain a complaint (deleted Reddit posts, Show-HN announcements, off-topic content) — a structurally valid but unanticipated response that crashed the cluster pipeline against the strict-string schema. The fix accepts null as a valid Haiku output and marks those signals as non-clusterable in `signal_text.is_low_signal`. Generalizable lesson: when integrating an LLM-judgment step, prefer schemas that can express "no judgment applicable" (Optional fields, sentinel values) over schemas that force the model to invent answers it doesn't have.

2. **`apfun/models/signal_text.py`** (or wherever `SignalText` lives) — docstring update for `is_low_signal` documenting the broadened meaning.

3. **`docs/tasks/010-stage1-clustering.md`** — Notes section addition documenting that `core_complaint` is `Optional` and the null-handling pattern. References to the Lesson Learned.

4. **`docs/orchestrator/INDEX.md`** — row 024 → answered after PR merges.

5. **No CLAUDE.md → Conventions changes.** The broadened `is_low_signal` meaning is a single-table semantic, not a project-wide convention.

## What I would do next without intervention

1. Branch `feature/task-010-fix-1-null-complaint`.
2. Update `SignalCoreComplaint.core_complaint` to `str | None`.
3. Update `dedup_signal.j2` prompt with the explicit "return null if no complaint" instruction.
4. Update `_haiku_prepass` per the shape above.
5. Run `_haiku_prepass` against existing test fixtures; should still pass.
6. Add the null-response fixture and tests.
7. Apply documentation updates.
8. Verify `grep -r '# TODO verify' apfun/` returns zero.
9. Open PR with: link to this orchestrator request, description of the production-bug-recovery framing, and a note that an operator manual cluster-rerun is the validation step post-merge.

## Specific questions or risks

1. **Q1 decision (column reuse vs new column).** My lean is (a) — reuse `is_low_signal` and document the broadening. Implementer is welcome to push back if (b) feels meaningfully cleaner; (c) is rejected.

2. **Are `vertical` and `keywords` also affected?** When `core_complaint` is null, those two are almost certainly also null (Haiku has no complaint to derive a vertical or keyword set from). Make them `Optional` in the schema too. Cluster pipeline already short-circuits on null `core_complaint`, so downstream uses won't see null `vertical`/`keywords` — but the schema should be permissive.

3. **Prompt update validation.** Adding "return null if no complaint" to `dedup_signal.j2` is small but real. The implementer should re-run the existing dedup-related tests with the new prompt to confirm Haiku still returns the right shape on *valid* complaint signals. If a test fixture is doing exact-string matching against Haiku output, it'll need a refresh capture.

4. **Edge case: ALL signals in a batch return null.** Cluster should log a clear warning (`"all N signals in batch had no clusterable complaint — no candidates produced"`), write a `scheduler_runs` row with `ok=True items_processed=N` (or whatever convention reflects "ran but produced nothing"), and complete cleanly. Don't treat this as an error.

5. **Cost cap during this fix's empirical validation.** When operator manually re-runs cluster against the 86 signal_text rows, mental cap of $2. At Haiku's ~$0.001/signal × 86 ≈ $0.09 worst case, plus some Opus calls if any clusters emerge, total should be well under $1. If it exceeds $2, something is wrong — abort and investigate.

## Relevant files

Code under change:
- `apfun/models/cluster.py` (or wherever `SignalCoreComplaint` lives) — schema change
- `apfun/llm/prompts/dedup_signal.j2` — prompt update
- `apfun/pipeline/cluster.py` `_haiku_prepass` — null handling
- `apfun/models/signal_text.py` — docstring update for `is_low_signal`
- `tests/unit/test_cluster.py` (or equivalent) — null-handling tests
- `tests/fixtures/llm/haiku_dedup_null.json` (or equivalent) — null-response fixture

Docs under change:
- `CLAUDE.md` — Lesson Learned entry
- `docs/tasks/010-stage1-clustering.md` — Notes addition
- `docs/orchestrator/INDEX.md` — row 024 → answered

## Empirical validation (post-merge)

After the PR merges, the operator re-runs the cluster pipeline against the existing 86 signal_text rows:

```python
from apfun.db import SessionLocal
from apfun.pipeline.cluster import cluster_signals
from apfun.llm.client import LLMClient

with SessionLocal() as s:
    print(cluster_signals(s, llm_client=LLMClient()))
    s.commit()
```

Expected: some signals get marked `is_low_signal=True` post-Haiku, the remainder cluster into candidates. Operator inspects how many were null-judged vs clustered; if the null rate is surprisingly high (>50%), that's a signal that *either* the funnel's incoming content is lower-quality than expected *or* Haiku's null-threshold is too aggressive (prompt may need tightening to recognize implicit complaints).

If null rate is reasonable (~10-30%), the fix is working as designed. Open a short orchestrator turn with the operator's findings.

## Meta note

This is the fourth empirically-discovered bug (per the count in my immediately-prior orchestrator turn). Per the meta-discipline emerging from feedback 018 onward, each stage gets a runbook before scheduler integration — but Stage 1 had its runbook (runbook 001), and this bug surfaced *post-integration* on real Reddit data the runbook didn't include.

The right generalization: **runbooks catch the bugs visible against the data shapes they test. Real production data extends those shapes; some bugs only surface when the funnel sees content the operator didn't curate for the runbook.** This isn't a discipline failure — it's the discipline working at one level (catching what's catchable per-runbook) while a deeper layer (real production data) reveals the next set.

For future stages: budget for ~1-2 post-integration bugfix orchestrator turns per stage, in addition to the pre-integration runbook. Stage 1 has now had its post-integration turn; Stage 2/3/4/5 will likely each have theirs.
