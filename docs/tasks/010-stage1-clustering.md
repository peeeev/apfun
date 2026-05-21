# 010 — Stage 1 clustering

**Goal:** Turn batches of unclustered signals into coherent `candidates` (idea cards). First LLM-in-production stage. Reads `signal_text` (the uniform projection from task 010a); per orchestrator feedback 016.

**Complexity:** M-L

Depends on: 004 (LLM wrapper), 010a (signal_text normalization), and at least one ingester (005-009).

## Read shape

Stage 1 reads `signal_text`, not `raw_signals.payload_json`. The join to `raw_signals` is for metadata only (`url`, `captured_at`):

```sql
SELECT signal_text.*, raw_signals.url, raw_signals.captured_at
FROM signal_text JOIN raw_signals ON signal_text.raw_signal_id = raw_signals.id
WHERE signal_text.is_low_signal = FALSE
  AND signal_text.raw_signal_id NOT IN (SELECT raw_signal_id FROM candidate_signals);
```

- **Skip `is_low_signal=True`** (currently only Reddit `[deleted]`). Stage 4 saturation scoring may revisit these for *secondary* weighting later; as Stage 1 input they degrade cluster quality more than they contribute. Per feedback 016 Q6.
- **Skip rows already in `candidate_signals`** (the `candidate_signals.created_at` column exists for the "signals since rejection" UI computation and for manual-re-cluster: operator deletes the relevant rows, next Stage 1 run treats them as unclustered). Per feedback 016 Q8.

## Pipeline

### 1. Haiku pre-pass

For each unclustered signal, call `mechanic_json("dedup", schema=SignalCoreComplaint, ...)` returning:

```python
class SignalCoreComplaint(BaseModel):
    core_complaint: str      # normalized one-sentence summary
    vertical: str            # suggested vertical slug
    keywords: list[str]      # 3-5 normalized keywords (lowercase, deduplicated)
```

The `keywords` are the load-bearing field for bucketing (per feedback 016 Q1).

### 2. Bucketing — keyword-set, deterministic

Bucket key: `(vertical, frozenset(keywords))`. The `frozenset` is the deterministic shape — sort the keyword set before hashing so `["billing", "stripe"]` and `["stripe", "billing"]` always land in the same bucket. Add a unit test pinning this.

> Buckets are a cost-shaping tool, not a quality-defining one. Coarse → Opus inside separates unrelated items. Fine → pass 2 merges related items. Either failure mode degrades cost, not quality. (Per feedback 016 Q1.)

Cap buckets per run at `_MAX_BUCKETS_PER_RUN = 50` (one Opus call each). If signal-count exceeds `_MAX_SIGNALS_PER_RUN = 500`, also pause. Soft-cap behavior: log warning, record cap-hit in `scheduler_runs`, return cleanly. Retune trigger: consecutive cap-hits → open orchestrator request.

### 3. Opus per-bucket (pass 1)

For each bucket, call `judge_json("cluster", schema=ClusterOutput, cache_ttl="1h", ...)`:

```python
class IdeaCard(BaseModel):
    problem_statement: str
    suspected_user: str | None
    seed_keywords: list[str]
    contributing_signal_ids: list[int]   # raw_signal_id values

class ClusterOutput(BaseModel):
    clusters: list[IdeaCard]
```

Prompt template `apfun/llm/prompts/cluster.j2`. Explicit about: don't invent ideas not present in signals, return strict JSON, every `contributing_signal_ids` entry must be one of the IDs in the input batch.

`cache_ttl="1h"` because cluster batches can exceed the 5-min default cache TTL when there are many buckets.

### 4. Cross-chunk merge (pass 2, when needed)

If a single bucket exceeds ~150k tokens, chunk and run pass-1 per chunk. Then call `judge_json("cluster", schema=ClusterMergeOutput, ...)` with only cluster *titles + seed_keywords* (not full evidence — keeps pass-2 input small per feedback 016 Q4):

```python
class ClusterMergeOutput(BaseModel):
    merge_map: dict[str, str]   # pass-1 cluster_id → canonical cluster_id
```

Application code rewrites `candidate_signals` to point at canonical clusters. Same task tag (`"cluster"`) for both passes — one model-policy entry, one task tag. Per feedback 016 Q4.

### 5. Persist candidates

For each emitted `IdeaCard`:

- Compute `dedup_key` = slug of `problem_statement`.
- **If a candidate with that `dedup_key` exists** (any `decision`): link the new signals via `candidate_signals` rows. Do NOT modify the existing candidate's `decision`. The HITL convention is durable — see CLAUDE.md → "HITL decisions are durable." Per feedback 016 Q5.
- **Otherwise** insert a new `Candidate(decision='pending', pipeline_stage='none', ...)` and link signals.

## Deliverables

- `apfun/pipeline/cluster.py` with `cluster_signals(session, *, llm_client) -> ClusterResult` entry point. Reads unclustered signals, runs the pipeline above, persists candidates, writes one `scheduler_runs` row.
- `apfun/llm/prompts/cluster.j2` — pass-1 prompt with strict-JSON instructions.
- `apfun/llm/prompts/cluster_merge.j2` — pass-2 merge-map prompt.
- `scripts/replay_clustering.py` — takes a `signal_text` snapshot, runs Stage 1 against it, dumps output. Lets prompt iteration happen without re-running upstream. Per feedback 016 risk-profile note.

## Acceptance

- Unit test with a stubbed `LLMClient` asserts: given a fixture batch of 10 signals, the loop calls `mechanic_json` 10×, buckets into N, calls `judge_json` N times, persists candidates, links signals correctly.
- Bucket determinism: `frozenset(["billing", "stripe"])` and `frozenset(["stripe", "billing"])` produce the same bucket key.
- Dedup-to-rejected: when a fixture `dedup_key` matches a candidate with `decision='rejected'`, new signals link to it but `decision` stays `'rejected'`.
- Idempotency: re-running on the same `signal_text` rows produces zero new candidates (skip-already-clustered is correct).
- Cap behavior: with `_MAX_BUCKETS_PER_RUN=2` and 5 buckets, only 2 are processed; remaining show up in `scheduler_runs` and process next run.
- `JSONParseError` retry: a stubbed LLM returning bad JSON first then good JSON triggers retry and succeeds; final-failure logs truncated response into `llm_runs.error`.
- Integration test (opt-in, gated on `APFUN_ANTHROPIC_API_KEY`) runs end-to-end on 20 fixture signals and produces ≥1 candidate.
- `grep -r '# TODO verify' apfun/ tests/ scripts/` returns zero.

## Notes

- Per CLAUDE.md model policy: `cluster` task → `judge()`. Never re-point at Haiku. `cluster` is in `JUDGMENT_TASKS` (already).
- `cache_ttl="1h"` is used in `judge_json` calls; `PRICING['claude-opus-4-7']['cache_write_1h'] = 10.00` reflects the higher write-cost tier.
- Cost ceiling is soft (cap + log + pause); no monthly hard ceiling yet. Acceptable for v1; revisit when usage exists.
- Post-merge action item (per feedback 016 risk profile): after first scheduled run, open a brief orchestrator request with `llm_runs` cost numbers + bucket-count distribution. Validates PRICING and informs first thinking-budget retune.
- Prompts will need 2-3 rounds of refinement against real data. `replay_clustering.py` exists so refinement doesn't require re-running the upstream pipeline.
