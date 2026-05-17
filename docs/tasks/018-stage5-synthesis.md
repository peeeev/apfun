# 018 — Stage 5 differentiation synthesis

**Goal:** The most important LLM step in the system. Take the full review corpus + feature matrix + pricing + SERP and synthesize a differentiation angle.

Depends on: 017.

## Deliverables
- `apfun/synthesis/differentiate.py`:
  - Assemble the input bundle for a candidate: competitive_analyses (pricing, features, funding), scored complaints, raw negative reviews (sampled to fit context), demand_checks summary.
  - Single Opus 4.7 xhigh call (`judge("synthesize", ...)`) with the bundle in cached blocks. Strict JSON schema:
    ```json
    {
      "top_complaints": [{"theme": "...", "evidence_review_ids": [...], "severity": 1-5}, ...],
      "feature_gaps": [{"gap": "...", "present_in": ["competitorA"], "absent_in": ["competitorB", "competitorC"]}, ...],
      "pricing_gaps": [{"description": "missing $X/mo tier", "evidence": "..."}, ...],
      "vertical_wedge": {"vertical": "...", "rationale": "...", "underserved_signals": [...]}
    }
    ```
  - Persist an `opportunities` row (one per candidate; UNIQUE FK).
- Prompt template at `apfun/llm/prompts/synthesize.j2` is explicit about: every claim must cite a competitor/review/keyword from the input bundle; no invention; output strict JSON.

## Acceptance
- Integration test (opt-in, real API) on one fully-populated candidate produces a valid `opportunities` row whose JSON validates against the schema.
- Re-running on the same candidate updates the existing `opportunities` row rather than inserting a duplicate.
- Failure mode: if the LLM returns invalid JSON, the call is retried once with `repair_json=true`, then logs an `llm_runs` error and marks the candidate `status=synthesis_failed`.

## Notes
- This is the highest-value Opus call in the whole system. Burn tokens here — extended thinking budget should be ample.
- Use prompt caching on the review corpus + feature matrix; they're the same across the few retries.
