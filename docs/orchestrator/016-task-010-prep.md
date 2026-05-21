# Request 016: task 010 prep — Stage 1 clustering design questions

**Date:** 2026-05-21

**Context**: Task 010a (signal_text normalization) merged via PR #7. Five ingesters feed `raw_signals`; normalizer projects them into uniform `signal_text`. Task 010 (Stage 1 clustering) is the first LLM-in-production stage — `judge()` with Opus 4.7 actually called from a non-test code path. The task spec was written before tasks 005-010a existed, so reconciliation with `signal_text` is itself a design question, plus several genuinely-open choices around bucketing, output validation, and cost discipline. Surfacing before any code lands.

## What landed in tasks 009 + 010a (orchestrator can't see PRs)

### Task 009 — review miner (PR #6, merged at `806ca36`)

Already summarized in request 015. Recap of the relevant bits for Stage 1: review_sites populates `raw_signals` with `payload.helpful_count` (strongest weight signal); per-site adapters in `apfun/sourcing/review_sites/{g2,capterra,trustpilot}.py`; CSV importer `scripts/import_reviews.py` as anti-scraping fallback; risk-profile guidance from feedback 014 baked into the integration-test failure message.

### Task 010a — signal_text normalization (PR #7, merged at `1c5302c`)

- **`apfun/models/signal_text.py`** + Alembic `cda196c18c5a`: `SignalText(id, raw_signal_id UNIQUE, source_kind, text, social_proof_weight, is_low_signal, extracted_at)` with FK ON DELETE CASCADE from `raw_signals`. `UNIQUE(raw_signal_id)` is the idempotency guard.
- **`apfun/pipeline/_extractors.py`**: 5 per-source extractors returning `ExtractedText(text, social_proof_weight, is_low_signal)`. Dispatch table `EXTRACTORS` at module scope. Weight formulas (Reddit `score + 2*num_comments`, HN `points + 2*num_comments`, PH `votesCount`, IH `upvoteCount + 2*replyCount`, reviews `helpful_count`) carry `# heuristic 2026-05-19` annotations with retune-trigger language pointing at Stage 4 (task 014).
- **`apfun/pipeline/normalize.py`**: `normalize_raw_signals(session, *, batch_size=500, only_new=False) -> NormalizeResult`. Idempotent ETL — re-running updates by `raw_signal_id` rather than inserting duplicates. Writes one `scheduler_runs` row per invocation (`job_id="pipeline.normalize"`).
- **`is_low_signal`** flag is set on Reddit `is_deleted=True` rows. No other source currently sets it.
- 29 new tests, 169 total unit tests pass.

## Reconciling task 010 spec with what now exists

The task 010 spec reads as if Stage 1 operates on `raw_signals.payload_json` directly. Now that `signal_text` exists, the natural read shape is:

```sql
SELECT signal_text.id, signal_text.text, signal_text.social_proof_weight,
       signal_text.source_kind, signal_text.is_low_signal,
       raw_signals.url, raw_signals.captured_at, raw_signals.id AS raw_signal_id
  FROM signal_text JOIN raw_signals ON signal_text.raw_signal_id = raw_signals.id
  WHERE signal_text.is_low_signal = FALSE
    AND signal_text.id NOT IN (SELECT candidate_signal.raw_signal_id FROM candidate_signals)  -- "unclustered"
```

That's what I'd read. **Confirm:** Stage 1 reads `signal_text`, not `raw_signals.payload_json`. Skip rows with `is_low_signal=True`. Skip rows already linked to a candidate via `candidate_signals`.

## Specific questions

### Q1 — Bucketing: keyword-set or embedding?

Spec says bucket by `(vertical, core_complaint_embedding_or_keyword_set)`. Hedging between two distinct mechanisms.

- **(a) Keyword-set buckets.** The Haiku pre-pass already emits a "suggested vertical" + a normalized "core complaint" sentence. Add a third field: a small set of 3-5 normalized keywords (e.g., `{"billing", "stripe", "proration"}`). Bucket by `(vertical, sorted_tuple_of_keyword_set)`. Cheap, deterministic, no model deps.
- **(b) Embedding buckets.** Run sentence embeddings (e.g., `sentence-transformers/all-MiniLM-L6-v2`) on each core complaint, cluster via cosine similarity (DBSCAN or threshold). More semantically robust, more moving parts.

**My lean: (a) keyword-set.** Reasons:
- No new dependency (no torch, no model download in the container).
- Haiku already emits the keywords — same call as core-complaint extraction.
- Bucketing precision doesn't need to be perfect — Opus inside the bucket is the real clustering step. The bucket is a cost-shrinking tool, not a quality-determining one.
- Embedding-based bucketing has a "model version" question (different embedding model → different clusters), which is more complexity than this stage needs at v1.

Confirm (a), or push back if (b)'s semantic robustness is worth the complexity.

### Q2 — Cache TTL extension (`cache_write_1h`)

Spec says: "if a clustering batch's wall-clock duration exceeds 5 minutes ... extend `apfun/llm/client.py::_build_system` to accept `ttl="1h"` and route the shared system prompt through it. Update `PRICING` to include `cache_write_1h` (Opus 4.7: $10/MTok)."

This is "do it now" vs "do it when measured." Two reads:
- **(a) Extend the wrapper in this PR.** Even if a single batch stays under 5 minutes, the system prompt for cluster is reusable across batches in the same scheduler run. Eager extension saves on every Stage 1 invocation that has >1 batch.
- **(b) Skip until measured.** YAGNI — if batches stay under 5 minutes AND we don't run multi-batch loops in production, no savings to capture.

**My lean: (a) extend now.** Reasons:
- It's a small wrapper change (~10 lines + a PRICING entry + a test).
- The shared system prompt for cluster is the kind of thing that benefits from cross-batch caching.
- Doing it later means a separate PR + retrofitting the call site.
- Cost-discipline-by-default is the project posture.

Confirm (a) or push back to (b).

### Q3 — Cluster output validation: SDK structured-outputs vs ad-hoc JSON parsing

Opus needs to emit `list[{problem_statement, suspected_user, seed_keywords, contributing_signal_ids}]`. Options:

- **(a) Anthropic SDK structured outputs / tool use.** Define a tool/schema and let Opus emit validated structured data. Cleanest, type-safe, fails loudly on schema violation.
- **(b) JSON in text + Pydantic validation on our side.** Prompt explicitly for JSON, parse with `json.loads`, validate against a Pydantic model. Retry-on-parse-failure inside the wrapper.

I haven't used Anthropic's structured-outputs path yet — task 004 (LLM wrapper) only covered the basic text-response path. Adding tool-use plumbing is a bigger lift than (b) for the same end-state. Plus Opus + extended thinking + tool use has more interactions to verify.

**My lean: (b) JSON in text + Pydantic.** Reasons:
- Minimal wrapper changes (no new tool-use code path).
- Pydantic validation gives the same loud-failure-on-shape-drift as tool use.
- Retry-on-parse-fail is mechanically simple; the LLM wrapper already does retries.

If you'd prefer (a), I'll lift tool-use into the wrapper as part of task 010 prep — it's a reasonable investment if structured outputs are the project's long-term shape. Confirm direction.

### Q4 — Multi-chunk batching: two-pass merge mechanics

Spec: "Cap a single Opus call's input at ~150k tokens. Chunk if needed; combine clusters across chunks via a second pass."

Two-pass mechanics aren't specified. My read:

- **Pass 1**: each chunk → Opus → list of clusters with their contributing signal_ids.
- **Pass 2**: combine cluster *titles* from all chunks → Opus → "are any of these the same cluster?" → merge map → relink signals to merged clusters.

Open: pass 2 is itself an Opus call (judgment), so add `task="cluster_merge"` to `JUDGMENT_TASKS`? Or is `task="cluster"` the right tag for pass 2 too since it's the same conceptual stage?

**My lean: keep `task="cluster"` for both passes.** Reasons:
- Both are clustering judgment — same model selection, same thinking budget.
- The retune-trigger discipline operates on task-level aggregates; splitting "cluster" and "cluster_merge" fragments the signal.
- One task tag, two prompt templates — `cluster.j2` for pass 1, `cluster_merge.j2` for pass 2.

Confirm or split.

### Q5 — `dedup_key` collision with rejected candidates

Spec: "if a new idea's dedup_key matches, link signals to the existing candidate instead of creating a new one."

What if the matching existing candidate has `decision='rejected'` (HITL rejection)? Three options:

- **(a) Link to the rejected candidate anyway.** Signals attach to the rejection so we can show "this idea has been seen N more times since you rejected it."
- **(b) Create a new pending candidate.** Treat rejected ideas as "should not come back"; force the new evidence into its own card.
- **(c) Create a new pending candidate AND link signals to both rejected and new candidates.** Best of both — preserves the "we already rejected this shape" history while exposing the new evidence to a fresh review.

**My lean: (a) link to the rejected candidate.** Reasons:
- It avoids duplicate-rejection fatigue (HITL approver sees "this rejected card got 7 new signals — change of mind?" rather than approving/rejecting a near-duplicate).
- The HITL UI (task 021) can surface "rejected, but X more signals" as a re-review prompt.
- (b) is the simplest but loses information.

Confirm or push back.

### Q6 — `is_low_signal` rows: skip, downweight, or include?

Currently set only on Reddit `is_deleted=True`. Three options:

- **(a) Skip entirely in Stage 1.** They have a title only; clustering on title is weaker. Simpler.
- **(b) Include them with lower weight.** Aggregate `social_proof_weight * is_low_signal_discount` somewhere. More signal, but the title-only text might pollute clusters.
- **(c) Include them in the cluster but tag the cluster's `low_signal_ratio`.** Useful for downstream Stage 4 weighting; doesn't change Stage 1 behavior.

**My lean: (a) skip entirely.** Reasons:
- Title-only text is genuinely weak for clustering.
- Deleted-post titles are the noisiest part of Reddit (often vague/meme).
- Adding low-signal handling at this layer is scope creep; Stage 4 saturation scoring is the right place.

Confirm (a) or push back.

### Q7 — Cost ceiling per Stage 1 invocation

Stage 1 will run on a schedule. Without a budget guard, an ingester producing 10,000 new signals between runs could trigger expensive batches.

Soft-cap suggestion: each scheduled Stage 1 invocation processes at most N new signals (e.g., 500). Excess gets picked up next run. Annotate as `# heuristic`. Caps growth in single-run cost.

**My lean: yes, add `_MAX_SIGNALS_PER_RUN = 500` with `# heuristic` annotation.** Retune trigger: when scheduler_runs shows Stage 1 hitting the cap consistently, open an orchestrator request rather than tuning silently.

Confirm or suggest a different cap.

### Q8 — HITL freshness expectation

`candidate.decision='pending'` + `pipeline_stage='none'` is the initial state. How quickly should HITL be expected to review? This affects whether Stage 1 should re-cluster the same `signal_text` rows into a *second* candidate if the first review hasn't happened in N days, OR whether it should permanently skip already-clustered signals.

**My lean: permanently skip already-clustered signals.** Reasons:
- Re-clustering produces noise — same signals → same cluster, just bumps a counter somewhere.
- HITL is the gate. If the gate is slow, the queue grows; that's the operator's signal to engage.

The `candidate_signals` table is the source of truth for "this signal has been considered." Stage 1 reads `signal_text WHERE id NOT IN (SELECT raw_signal_id FROM candidate_signals)`. Confirm.

## What I would do next without intervention

After Q1-Q8 land:

1. **Prep commit** — wrapper extension for `ttl="1h"` (if Q2=a) + `cluster_merge` prompt template scaffolding (if Q4 splits) + `JUDGMENT_TASKS` audit.
2. **`apfun/pipeline/cluster.py`** — `cluster_signals(session) -> ClusterResult`. Reads unclustered `signal_text` rows, calls `mechanic("dedup", ...)` per signal for normalized complaint + vertical + keywords, buckets, calls `judge("cluster", ...)` per bucket, persists `candidates` + `candidate_signals`, writes `scheduler_runs` row.
3. **`apfun/llm/prompts/cluster.j2`** — strict-JSON prompt, "don't invent ideas not present in signals," contributing_signal_ids as the load-bearing field.
4. **Tests** — stubbed-LLMClient unit test asserting Haiku-N + Judge-1 call counts, candidate+links persisted, dedup_key match → link-instead-of-insert; integration test with real Anthropic that hits a real Stage 1 batch end-to-end on captured fixture signals; idempotency test (re-run → zero new candidates).
5. **`scripts/seed_signal_text.py`** or fixture-based test data — needed if integration testing requires actual signal_text rows. Lean: build the seeded test database in a fixture, not a script.
6. **Cost telemetry** — confirm `llm_runs` has the right shape for Stage 1 cost reporting; if not, raise it as a follow-up rather than expanding 010 scope.

Expected: ~6 commits, single PR.

## Relevant files

- branch `feature/task-010-stage1-clustering` (currently just this request)
- `docs/tasks/010-stage1-clustering.md` — original spec
- `apfun/llm/client.py` — judge/mechanic, JUDGMENT_TASKS, _build_system (cache control)
- `apfun/models/candidate.py` — candidates schema (decision + pipeline_stage), candidate_signals join table
- `apfun/models/signal_text.py` — read shape for Stage 1
- `apfun/pipeline/normalize.py` + `_extractors.py` — upstream stage
- `docs/orchestrator/INDEX.md` — row 016 → open after this commit
