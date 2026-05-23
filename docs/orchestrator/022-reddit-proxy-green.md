# Request 022: runbook 003 green — close the loop on task 005c

**Context.** Task 005c (Reddit residential-proxy + browser-UA, PR #17) is merged. Runbook 003 ran green from this datacenter IP — the proxy + browser-UA path defeats both gating layers (datacenter-IP network block + June-2025 frontend UA filtering). Closing the loop per request 021 §Empirical validation.

**What I just did** (runbook 003, all green):

| Step | Result |
|------|--------|
| 1 — raw proxy fetch (operator) | `200` |
| 2 — single-source ingest (r/SaaS) | `status_codes=[200]`, 25 items captured |
| 3 — 3-sub batch (SaaS / Entrepreneur / startups) | all `[200]`; +25, +25, +0 (SaaS deduped — content-hash collision with step 2) |
| 4 — artifacts | `scheduler_runs` row `ok=1 items_processed=50`; 75 total Reddit rows in the DB |
| 5 — real-shape validation | real r/SaaS listing (25 children) validates against the contract-test required-field set — **no shape drift** |

No 403s, no 429s, no UA-block-guard firing. Webshare residential (free/low tier) + rotating Chrome UAs is sufficient at this volume. Cost: negligible (well under the $5 runbook budget).

**What I would do next without intervention:**

1. Mark task 005c done; Reddit sources are effectively back in rotation (the scheduler's `reddit.ingest_batch` job now succeeds).
2. The 75 real Reddit signals captured during the runbook stay in the DB — they'll normalize + cluster on the next scheduler cycle, feeding the 3-5-cycle routing review from feedback 019.
3. Resolve the fixture question (below) as a small follow-up PR.

**Specific questions / decisions:**

1. **Synthetic fixture refresh (runbook 003 Step 5).** I did NOT overwrite `tests/fixtures/reddit/listing_saas.json`. It's synthetic (`abc1`–`abc4`) and load-bearing — `test_reddit_ingester.py` asserts exact IDs (`t3_abc1`, a specific permalink) and the `[deleted]`/`[removed]` deletion markers on `abc2`/`abc3`. A blind real-data overwrite (as Step 5 literally instructs) breaks those value-assertions. The runbook author didn't account for the fixture being load-bearing for controlled-value tests. My recommended resolution: **keep the synthetic fixture for the value-asserting ingester tests; add a separate committed real capture (`listing_saas_real.json`) and point the schema-contract test at it** so contract-drift detection runs against real shape while value tests stay deterministic. Confirm this split, or prefer rewriting the ingester tests to be value-agnostic against a single real fixture?

2. **Does runbook-003 green change the Lesson Learned wording?** The "External-API policy changes can invalidate engineering effort / one symptom can mask multiple causes" entry already landed in CLAUDE.md (005c PR). It's framed around the policy change + two-causes, which holds regardless of the green outcome. I don't think it needs revisiting — flagging in case you want a green-specific addendum ("…and the residential-proxy + browser-UA workaround succeeded").

3. **Routing review timing.** Feedback 019's 3-5-scheduler-cycle / ~30-50-candidate routing review (011 Stage 2 vs 015 Stage 3) is the next real checkpoint. Reddit now contributes signal alongside HN, so candidate volume should climb faster than the HN-only baseline. No action requested — noting that this checkpoint is approaching and Reddit-in-rotation accelerates it.

**Relevant files:**
- `docs/operator/runbooks/003-reddit-proxy-first-pass.md` — the runbook just executed
- `tests/fixtures/reddit/listing_saas.json` — the synthetic fixture in question
- `tests/unit/test_reddit_ingester.py` — the value-asserting tests that depend on it
- PR #17 (merged) — task 005c
