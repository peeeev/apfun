# Request 027: runbook 004 — Stage 1 null-rate diagnosis

**Date:** 2026-05-23

**Context.** After task 024 (cluster null-complaint fix) shipped, the operator ran cluster against 75 newly-normalized signal_text rows from the past 24h (HN + scheduler-driven Reddit ingest). Outcome: **61 of 75 signals (81%) marked `is_low_signal=True` post-Haiku** — well above request 024's "reasonable 10-30%" range and over the 50% escalation threshold.

Per request 024:

> If null rate is reasonable (~10-30%), the fix is working as designed. Open a short orchestrator turn with the operator's findings.

The findings demand more than a short turn — 81% is a calibration problem, not noise. Diagnostic work needed before any prompt change.

## Two competing hypotheses

**(a) Reddit content is genuinely 81% non-complaint.** Lots of success stories ("I just launched X"), announcements, off-topic posts, generic help requests, casual discussion. Haiku is judging accurately and the signal-source mix needs upstream filtering rather than downstream tuning.

**(b) Haiku's complaint bar is too high.** It's missing implicit complaints — "What's the best way to do X?" structurally a question but semantically a need-for-solution. "Looking for tools that..." similar. The prompt asks Haiku to find *explicit* complaints when *implicit* ones might be the larger signal.

These are testable. We need to look at the actual nulled signals and judge them ourselves.

## Goal

Produce a runbook (`docs/operator/runbooks/004-stage1-null-rate-diagnosis.md`) that:

1. Surfaces all signals marked `is_low_signal=True` by Haiku (not by the structural normalize-time markers like `[deleted]`).
2. For each, displays the original text and the source kind/identifier.
3. Lets the operator make a per-signal judgment: was Haiku's "no complaint" call correct, or did it miss an implicit complaint?
4. Aggregates the judgments to determine which hypothesis dominates.

The runbook is the deliverable. After the operator executes it, the captured judgments flow back as the next orchestrator turn's input, which decides whether to tune the prompt, accept the high null rate, or do something hybrid.

## Scope

**In scope:**

- `docs/operator/runbooks/004-stage1-null-rate-diagnosis.md` — the runbook itself. Numbered procedure, expected outputs, what artifacts to capture.
- `scripts/dump_nulled_signals.py` — small read-only script that prints (or writes to a file) all signals where `is_low_signal=True` was set by the Haiku pre-pass (distinguishable from structurally-low-signal rows like Reddit `[deleted]`).
  - Output columns: `signal_text_id`, `source_kind`, `source_identifier` (subreddit name, HN search query, etc.), `text` (truncated to ~500 chars to keep output manageable), `raw_signal_url`.
  - Format: tab-separated or simple JSON-lines; operator-friendly.
- A judgment-capture format. Suggestion: the script outputs a CSV/TSV that the operator can open in any spreadsheet or text editor, with a blank "operator_judgment" column they fill in per row. Values: `correct_null` (Haiku was right) / `missed_complaint` (Haiku should've extracted something) / `unclear`.
- The runbook documents how to: (1) run the script, (2) review the output, (3) capture judgments, (4) compute the aggregate ratio, (5) send back to orchestrator.

**Out of scope:**

- Any prompt change to `dedup_signal.j2`. The whole point of this runbook is to inform that decision, not pre-empt it.
- Any code change to the cluster pipeline. Read-only diagnosis.
- Auto-classification of "implicit complaint" detection. The operator is the judge; we don't want Haiku judging itself.
- Bulk-reverting `is_low_signal=True` for the misjudged signals. If we end up tuning the prompt and want to re-Haiku those signals, that's a follow-up after the diagnosis is complete.

## Distinguishing structural-low-signal from Haiku-judged-low-signal

This is the one tricky bit. Currently `is_low_signal` is overloaded (per request 024's design decision Q1):

- *Structural* (set at normalize time): Reddit `[deleted]` / `[removed]` posts
- *Judgment* (set after Haiku returns null): no clusterable complaint

The script needs to surface *only the latter*. Two options:

- **(a)** Add a discriminator column like `is_low_signal_reason: 'structural' | 'haiku_null'` (small migration, cleaner long-term).
- **(b)** Use existing data shape: structurally low-signal rows have `text` matching `[deleted]` / `[removed]` markers; Haiku-judged rows have substantive text. Filter on `LENGTH(text) > 20 AND text NOT IN ('[deleted]', '[removed]')`.

**My lean: (b) for this diagnostic.** It's a one-time runbook query, not a long-term feature. Don't migrate the schema for a temporary diagnostic. If we later decide we want this distinction permanently (for analytics, for re-Haiku'ing, etc.), open a separate orchestrator turn.

## Implementation shape

`scripts/dump_nulled_signals.py` (rough sketch):

```python
from sqlalchemy import select, and_
from apfun.db import SessionLocal
from apfun.models.signal_text import SignalText
from apfun.models.raw_signal import RawSignal
import csv
import sys

with SessionLocal() as s:
    rows = s.execute(
        select(
            SignalText.id,
            SignalText.source_kind,
            SignalText.text,
            RawSignal.url,
            RawSignal.payload_json,  # for source_identifier extraction
        )
        .join(RawSignal, RawSignal.id == SignalText.raw_signal_id)
        .where(
            and_(
                SignalText.is_low_signal == True,
                SignalText.text.notin_(["[deleted]", "[removed]"]),
            )
        )
        .order_by(SignalText.source_kind, SignalText.id)
    ).all()

writer = csv.writer(sys.stdout, dialect="excel-tab")
writer.writerow(["signal_text_id", "source_kind", "source_identifier",
                 "text_preview", "url", "operator_judgment"])
for r in rows:
    src_id = _extract_source_identifier(r.source_kind, r.payload_json)
    preview = r.text[:500].replace("\n", " ")
    writer.writerow([r.id, r.source_kind, src_id, preview, r.url, ""])
```

`_extract_source_identifier()` should:
- For Reddit: `payload_json["subreddit"]` → "r/SaaS"
- For HN: `payload_json["_apfun_query"]` → "hn:wishes" or similar
- For ProductHunt: `payload_json["_apfun_surface"]` (per normalize.py extractors)
- For IH: `payload_json["_apfun_group"]`
- For review sites: `payload_json["site"]:payload_json["product_slug"]`

The runbook (`004-stage1-null-rate-diagnosis.md`) walks the operator through:

1. **Run the script:** `uv run python scripts/dump_nulled_signals.py > /tmp/nulled-signals.tsv`
2. **Open in spreadsheet** (or use `column -t -s$'\t'` for terminal viewing).
3. **For each row, fill in `operator_judgment`** with one of: `correct_null` / `missed_complaint` / `unclear`. Skip rows quickly — gut feel is fine. Target: 20-30 rows judged (sample is sufficient; no need for all 61).
4. **Aggregate:** count each judgment value, plus break down by source_kind.
5. **Capture back to orchestrator:** the aggregate counts + a few exemplar rows per category (especially `missed_complaint` ones — those tell us what Haiku's missing).

## What "winning" looks like

After the runbook executes, we know:

- **If `correct_null` dominates (>70%):** Haiku is right; Reddit content is mostly non-complaint. No prompt change needed. Next move: upstream source curation (maybe filter Reddit's `/new` listings differently, or restrict to higher-quality subreddits).
- **If `missed_complaint` dominates (>30%):** Haiku's prompt is too restrictive. Next move: tune `dedup_signal.j2` to recognize implicit complaints. Operator-judged examples become the new prompt's exemplars.
- **If mixed or unclear:** decide on a case-by-case basis. Maybe a small prompt tweak + accept moderate null rate.

The orchestrator turn that follows this runbook (call it request 028 or whatever number) writes the actual prompt change task based on the data.

## Tests

Minimal. The script is read-only and one-time-use:

- Unit test that `_extract_source_identifier()` returns expected values for each source kind's payload shape.
- Unit test that the script's query excludes structural-low-signal rows (`[deleted]` / `[removed]`).

The runbook itself isn't testable — it's a procedure for a human.

## Documentation updates (same PR)

1. **`docs/operator/runbooks/004-stage1-null-rate-diagnosis.md`** — the new runbook (this IS the deliverable).
2. **`docs/orchestrator/INDEX.md`** — row 027 → answered after PR merges.
3. **`docs/tasks/010-stage1-clustering.md`** Notes section — add a line: "Null rate diagnostic procedure: `docs/operator/runbooks/004-stage1-null-rate-diagnosis.md`. Execute when escalation threshold (50%) is exceeded."

No CLAUDE.md changes here.

## What I would do next without intervention

1. Branch `chore/runbook-004-null-rate-diagnosis`.
2. Write `scripts/dump_nulled_signals.py` per the sketch.
3. Test that it runs against the current DB and produces expected output (you have ~61 Haiku-nulled rows to test against right now).
4. Write `docs/operator/runbooks/004-stage1-null-rate-diagnosis.md` with the numbered procedure.
5. Add the minimal unit tests.
6. Update INDEX and task 010 Notes.
7. Open PR. Verify the script's output by eyeballing 2-3 rows.

## Specific questions or risks

1. **What if a "Haiku-judged" signal has text that *happens* to match a structural marker** (e.g., a Reddit post titled "My experience with [deleted] accounts")? Unlikely but possible. The filter `text NOT IN ('[deleted]', '[removed]')` only excludes *exact* matches. Acceptable for this diagnostic. Don't over-engineer.

2. **`source_identifier` extraction depends on per-source payload shape.** If any ingester changes its `payload_json` shape later, this script's extractor breaks. Acceptable; it's a one-time-use diagnostic, not production code.

3. **Operator-time cost.** Judging 20-30 signals at ~30 seconds each = 10-15 minutes. Plus 5 minutes to run the script + capture aggregates. Total budget: 30 minutes. If it ends up taking 2 hours, the runbook is wrong-shaped — surface that.

4. **Sampling vs full-coverage.** I'm suggesting 20-30 rows (sample) rather than all 61. Sample is sufficient for routing-decision purposes; full coverage is over-investment. If the operator wants to judge all of them anyway, that's fine — just don't *require* it.

5. **Re-Haiku'ing later.** If we decide the prompt was too restrictive and want to re-process the misjudged signals, that's a follow-up task. Not in this PR's scope. The data needed to do that re-Haiku later is preserved in the DB — we don't lose anything by deferring.

## Relevant files

Code under change:
- `scripts/dump_nulled_signals.py` — new
- `tests/unit/test_dump_nulled_signals.py` — new

Docs under change:
- `docs/operator/runbooks/004-stage1-null-rate-diagnosis.md` — new
- `docs/orchestrator/INDEX.md` — row 027 → answered
- `docs/tasks/010-stage1-clustering.md` — Notes addition

## Empirical loop

The runbook executes → operator captures judgments → next orchestrator turn (with the data in hand) decides:

- Prompt change to `dedup_signal.j2` (most likely outcome)
- Upstream source filtering (if specific source kinds dominate the `correct_null` bucket)
- Both
- Neither (accept the null rate; move on)

The follow-up task ID is undetermined — depends on what the data says.
