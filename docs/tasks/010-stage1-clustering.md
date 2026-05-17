# 010 — Stage 1 clustering

**Goal:** Turn batches of unclustered `raw_signals` into coherent `candidates` (idea cards).

**Complexity:** M

Depends on: 004, plus at least one ingester (005–009).

## Deliverables
- `apfun/pipeline/cluster.py`:
  - Pre-pass with Haiku (`mechanic("dedup", ...)`): for each new signal, generate a normalized "core complaint" sentence + suggested vertical. Cheap, mechanical.
  - Bucket signals by `(vertical, core_complaint_embedding_or_keyword_set)` into batches of ≤30.
  - For each batch, run `judge("cluster", ...)` (Opus 4.7 xhigh) with the full signal text and ask for: list of distinct idea cards, each `{problem_statement, suspected_user, seed_keywords, contributing_signal_ids}`.
  - Persist new `candidates` (defaults: `decision='pending'`, `pipeline_stage='none'`) and `candidate_signals` links.
- Prompt template `apfun/llm/prompts/cluster.j2` — explicit about: don't invent ideas not present in the signals, return strict JSON, mark contributing signal IDs.
- Deduplication against existing candidates via `dedup_key` (slug of problem_statement) — if a new idea's dedup_key matches, link signals to the existing candidate instead of creating a new one.

## Acceptance
- Unit test with a stubbed `LLMClient` asserts: given a batch of 10 fixture signals, the loop calls `mechanic` 10× and `judge` once, persists N candidates, and links signals correctly.
- Integration test (opt-in, hits real Anthropic API) runs end-to-end on 20 fixture signals and produces ≥1 candidate.
- Re-running on the same signals does not create duplicate candidates.

## Notes
- Stage 1 is one of the three "judgment" stages. Never let `judge` get re-pointed at Haiku here.
- Cap a single Opus call's input at ~150k tokens. Chunk if needed; combine clusters across chunks via a second pass.
