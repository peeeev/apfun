# Runbook 004 — Stage 1 null-rate diagnosis

**Goal:** figure out *why* Haiku marked 81% of recent signals non-clusterable (`is_low_signal=True` post-pre-pass) — well over request 024's 50% escalation threshold. Two competing hypotheses, decided by looking at the actual nulled signals:

- **(a) Reddit content is genuinely ~81% non-complaint** (success stories, "I just launched X", off-topic, generic help). Haiku is judging accurately → the fix is upstream source curation, not a prompt change.
- **(b) Haiku's complaint bar is too high** — missing *implicit* complaints ("What's the best way to do X?" / "Looking for tools that…"). The prompt asks for explicit complaints when implicit ones are the larger signal → the fix is tuning `dedup_signal.j2`.

This runbook produces the operator judgments that route the follow-up.

**Budget:** ~30 minutes of operator time. No API spend (read-only DB queries). If it balloons past 30 min, the runbook is wrong-shaped — say so.

**Prerequisites:** the cluster pipeline has run against real data (you have ~61 Haiku-nulled rows right now). Run inside the container at `/workspace`.

---

## Step 1 — dump the Haiku-nulled signals

```bash
uv run python scripts/dump_nulled_signals.py --out /tmp/nulled-signals.tsv
```

Expected: prints `dumped N Haiku-nulled signals` to stderr (N ≈ 61 right now). The TSV has columns: `signal_text_id`, `source_kind`, `source_identifier`, `text_preview`, `url`, `operator_judgment` (blank).

The script excludes *structurally* low-signal rows (Reddit `[deleted]`/`[removed]`) so you only see signals Haiku judged — not normalize-time markers.

## Step 2 — review the output

Open it in a spreadsheet (import as tab-separated), or in the terminal:

```bash
column -t -s$'\t' /tmp/nulled-signals.tsv | less -S
```

## Step 3 — judge each row

Fill the `operator_judgment` column with one of:

- **`correct_null`** — Haiku was right, no clusterable complaint here.
- **`missed_complaint`** — there *is* an implicit need/complaint Haiku should have extracted.
- **`unclear`** — genuinely ambiguous.

Go fast — gut feel is fine. **Target 20–30 rows** (a sample is sufficient for the routing decision; you don't need all 61). If a row's `url` helps, open the original post.

## Step 4 — aggregate

Count each judgment value, and break the counts down by `source_kind`. A quick way once you've filled the column:

```bash
cut -f2,6 /tmp/nulled-signals.tsv | tail -n +2 | sort | uniq -c | sort -rn
```

(That's `source_kind` × `operator_judgment` frequency.)

## Step 5 — send back to the orchestrator

Capture and report:

1. **Aggregate counts**: how many `correct_null` / `missed_complaint` / `unclear`, and the % of each.
2. **Breakdown by source_kind** (is the null rate concentrated in Reddit? HN? a specific subreddit?).
3. **3–5 exemplar `missed_complaint` rows** verbatim (their `text_preview`) — these tell us what phrasing Haiku is missing and become the new prompt's examples if we tune.

## What the result routes to

- **`correct_null` dominates (>70%)** → Haiku is right; the content mix is the problem. Next move: upstream source curation (different Reddit listings, higher-quality subreddits). No prompt change.
- **`missed_complaint` dominates (>30%)** → the prompt is too restrictive. Next move: tune `dedup_signal.j2` to recognize implicit complaints, using your exemplars.
- **Mixed / unclear** → case-by-case; likely a small prompt tweak + accepting a moderate null rate.

The follow-up orchestrator turn writes the actual change (prompt edit, source filtering, or both) from your data. This runbook deliberately makes **no** prompt or pipeline change — it only gathers the evidence.

## Note on re-processing

If we later tune the prompt and want to re-Haiku the misjudged signals, that's a follow-up task — the data's preserved in the DB, nothing is lost by deferring. Don't bulk-revert `is_low_signal` as part of this runbook.
