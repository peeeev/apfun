# Feedback 016 — Stage 1 clustering design

**Date:** 2026-05-21
**Request:** 016-task-010-prep.md
**Outcome:** Reconciliation confirmed (Stage 1 reads signal_text). Eight questions answered with refinements. One new convention (HITL decisions are durable). Stage 1 risk profile flagged separately.

## Reconciliation

**Confirmed:** Stage 1 reads `signal_text`, not `raw_signals.payload_json`. The join to `raw_signals` is for metadata (`url`, `captured_at`) only.

Update the task 010 spec inline as part of the prep commit. The original spec predates `signal_text`; future readers shouldn't have to reconcile.

Stage 1 read query:

```sql
SELECT signal_text.*, raw_signals.url, raw_signals.captured_at
FROM signal_text JOIN raw_signals ON signal_text.raw_signal_id = raw_signals.id
WHERE signal_text.is_low_signal = FALSE
  AND signal_text.raw_signal_id NOT IN (SELECT raw_signal_id FROM candidate_signals);
```

## Answers

### Q1 — Bucketing: keyword-set confirmed (a)

Reinforcing argument worth recording in design notes:

> **Buckets are a cost-shaping tool, not a quality-defining one.** Coarse buckets → Opus inside separates unrelated items (quality preserved, cost slightly higher). Fine buckets → pass 2 merges related items (quality preserved, cost slightly higher). Either failure mode degrades cost, not quality. So pick *whatever's cheapest to compute and roughly approximates similarity*.

Keyword-set fits exactly. Embeddings would be more "semantically correct" but the correctness doesn't matter for this stage's job.

**Implementation note:** sort the keyword set deterministically before hashing into the bucket key. `frozenset(["billing", "stripe"])` and `frozenset(["stripe", "billing"])` produce the same bucket. Looks obvious; gets forgotten. Add a unit test.

### Q2 — Cache TTL extension: confirmed (a), as a config knob

Extend the wrapper now. **Treat the 1h TTL as a config knob, not a hardcoded path:**

```python
def judge(self, ..., cache_ttl: Literal["5m", "1h"] = "5m"):
    ...
```

Default stays 5-minute (no behavior change for current callers). Stage 1 explicitly passes `cache_ttl="1h"`. Future stages decide per their batch shape — Stage 5 synthesize might want 5m (single call, no batching); Stage 1 cluster wants 1h.

PRICING update:

```python
PRICING = {
    "claude-opus-4-7": {
        "input_per_mtok": ...,
        "output_per_mtok": ...,
        "cache_read_per_mtok": ...,
        "cache_write_5m_per_mtok": ...,
        "cache_write_1h_per_mtok": 10.00,   # 1h tier rate
    },
    ...
}
```

Bump `# verified` annotation to today's date with the verification source URL. Update `est_cost_usd` calculation to read the right `cache_write_*_per_mtok` based on what was sent.

### Q3 — JSON-in-text + Pydantic: confirmed (b), with robustness additions

Three additions to make (b) production-quality:

**Wrap parse failures as a retryable error class.** Make `JSONParseError` recognized by the wrapper's retry loop separately from API errors. Bad-JSON-from-Opus is rare with a good prompt but not zero — `extended_thinking` can occasionally leak structured reasoning into response text.

**Use Pydantic's `model_validate_json` directly,** not `json.loads` → validate. Better error messages, canonical pattern.

**Log the raw response on parse failure.** Truncate to 2k chars into `llm_runs.error` so debugging has an artifact. Without this, parse failures are mystery boxes.

```python
try:
    result = ClusterOutput.model_validate_json(response_text)
except ValidationError as e:
    raise JSONParseError(
        f"cluster response did not match schema: {e}\n"
        f"Response (truncated): {response_text[:2000]}"
    )
```

If Anthropic's tool-use becomes the right pattern later (some stage genuinely benefits from server-enforced schema), migrate then. Not now.

### Q4 — `task="cluster"` for both passes: confirmed

Two passes, one conceptual stage, one model-policy entry, one task tag. Reasoning is correct.

**Refinement on merge mechanics:** pass 2 receives only the cluster *titles + seed_keywords* from pass 1, not the full per-signal evidence. That keeps pass 2's input small. The output is just a merge map:

```python
{cluster_id_in_pass_1: canonical_cluster_id_after_merge}
```

Application code rewrites `candidate_signals` to point at canonical clusters.

**Don't have pass 2 re-cluster from scratch** — defeats the chunking optimization and introduces unbounded variance.

Draft the `cluster_merge.j2` prompt in the prep commit so reviewing it is separable from the clustering algorithm code.

### Q5 — Link to rejected candidates: confirmed (a), with critical refinement

Confirm linking new signals to the rejected candidate. **Critical refinement: don't auto-resurrect to `pending`.** The rejected candidate stays rejected. The HITL UI surfaces "this rejected card has accumulated N new signals since rejection — re-review?" but the *decision* itself doesn't flip automatically.

**Why this matters:** silently flipping `decision='rejected' → 'pending'` because new signals arrived erodes trust in HITL decisions. The operator rejected for a reason; new evidence should prompt a *deliberate* re-review, not auto-undo.

**Schema impact:** no new column needed. The UI computes `signals_since_rejection` by joining `candidate_signals.created_at > approvals.decided_at` for that candidate.

**New convention** (add to CLAUDE.md → Conventions):

> **HITL decisions are durable.** New evidence prompts re-review but never auto-flips a decision. The operator rejected for a reason; only an explicit re-decision changes the status.

### Q6 — is_low_signal: skip, confirmed (a)

Reasoning correct. Annotation suggestion for the spec:

> Stage 1 skips `is_low_signal = TRUE`. Deleted/removed Reddit posts (currently the only case) have title-only text with no problem statement to cluster on. Stage 4 saturation scoring may revisit these for *secondary* weighting signals ("this complaint was discussed enough that the post got removed") — but as input to clustering, they degrade cluster quality more than they contribute.

Future-you reading this knows the skip is deliberate and what the future option is.

### Q7 — Cost cap: confirmed, with refinement on cap shape

Confirm adding a cap. **Refinement: cap by *bucket count* primarily, signal count secondarily.**

Reasoning: cost scales with bucket count (one Opus call per bucket), not signal count. 500 signals in 3 buckets = 3 Opus calls (cheap). 500 signals in 200 buckets = 200 Opus calls (expensive).

```python
_MAX_BUCKETS_PER_RUN = 50   # one batch of Opus thinking budget
_MAX_SIGNALS_PER_RUN = 500  # secondary; prevents pathological Haiku passes

# heuristic 2026-05-21 — buckets dominate cost (one Opus call each).
# Signal count drives Haiku cost which is ~50x cheaper per token.
# Hit either cap → pause; excess processes next run.
```

Soft-cap behavior: log warning, record cap-hit in scheduler_runs, return cleanly. **Retune trigger:** consecutive runs capping out means catching up is needed — open orchestrator request to either bump caps or schedule more often.

### Q8 — Permanently skip already-clustered signals: confirmed

Reasoning correct. **One operational add:** track `candidate_signals.created_at` so "permanently skip" can be undone deliberately. If a cluster ever needs re-doing, the operator deletes the relevant `candidate_signals` rows; new Stage 1 runs see those signals as unclustered. Mechanism stays manual — automatic re-clustering recreates the noise problem.

Check `candidate_signals` schema for `created_at`; add via migration if missing.

## Stage 1 risk profile (aside)

This is the project's first real LLM-in-production stage. Three things worth flagging beyond the questions above.

### Prompt iteration will dominate Stage 1's iteration time

`cluster.j2` and `cluster_merge.j2` will need real-data tuning — "Opus is making up problem statements not present in signals," "Opus is splitting things that should cluster," etc. Plan to ship Stage 1 with prompts that work on fixtures, then expect 2-3 rounds of prompt-refinement against live data before quality settles.

**Build `scripts/replay_clustering.py` early.** Takes a `signal_text` snapshot, runs Stage 1 against it, dumps output. Lets prompt iteration happen without re-running the whole upstream pipeline. Worth half a day of investment in the task PR.

### First-real-cost measurement is now

After task 010 ships and runs once on real data, `llm_runs` has its first non-trivial entries. **Open a brief orchestrator request after the first scheduled run** with cost numbers and bucket-count distribution. Confirms PRICING was right; flags if thinking budgets need first retune ahead of schedule.

### Cost-monitoring hard ceiling — known gap

Beyond the soft cap from Q7, no absolute "stop spending if monthly total exceeds X" guard exists. Fine for v1 if you trust the soft caps. Monthly hard ceiling would go through `api_usage` (exists from task 003) consulted by the LLM wrapper before each `judge()` call.

Leave for follow-up unless you want it now.

## Action items

### Prep commit

1. Update task 010 spec to reflect `signal_text` read shape.
2. Wrapper extension: `cache_ttl: Literal["5m", "1h"]` knob on `judge()` and `_build_system`. PRICING `cache_write_1h_per_mtok = 10.00` for Opus, `# verified 2026-05-21`.
3. Pydantic schema for cluster output + `JSONParseError` class. Wrapper retries `JSONParseError` separately from API errors. Log truncated raw response on parse failure.
4. Caps: `_MAX_BUCKETS_PER_RUN = 50`, `_MAX_SIGNALS_PER_RUN = 500`, both `# heuristic`.
5. CLAUDE.md addition: "HITL decisions are durable; new evidence prompts re-review but never auto-flips a decision."
6. Migration if needed: confirm `candidate_signals.created_at` exists.

### Implementation commit

7. `apfun/pipeline/cluster.py` with all design choices above.
8. `apfun/llm/prompts/cluster.j2` + `cluster_merge.j2`.
9. `scripts/replay_clustering.py` for prompt iteration.
10. Tests: stubbed-LLMClient counts, dedup_key linking to rejected (no decision flip), idempotency, cap-hit behavior, JSONParseError retry behavior, deterministic keyword-set bucketing.

### Post-merge

11. After first scheduled run on real data: short orchestrator request with `llm_runs` cost numbers + bucket count distribution. Validates PRICING and informs first thinking-budget retune (per feedback 005's retune triggers).

## Next step + sequencing thought

Task 010 is the most substantive task since the LLM wrapper itself; expect this PR to be larger than recent ones. Don't let scope creep — the spec is already rich.

**After 010 ships, consider prioritizing task 013 (admin UI inbox) over task 011 (Stage 2 demand check).** Reason: until HITL is exercised, candidates accumulate without review and we don't learn whether clusters are even reviewable. The admin UI is where clustering quality becomes legible. Demand check matters too but operates on already-approved candidates, so it has no leverage on the unknown question ("are my Opus clusters good enough to be worth reviewing?").

Raise this in a future orchestrator request when 010 closes; I'm flagging it now so it's on your radar.

## Meta note

This request was the densest yet — eight genuine questions, each with non-obvious answers. The pattern of pre-implementation orchestrator turns absorbing design surprises is working at scale. The Stage 1 stage's risk is concentrated in the prompts, not the code; budget your post-merge attention accordingly.
