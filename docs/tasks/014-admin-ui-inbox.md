# 014 — Admin UI inbox

**Goal:** `GET /inbox` lists Stage 2 survivors (`candidates.decision='pending'` joined to a passing `demand_checks` row) as cards. Each card supports approve / reject / comment without a page reload. Keyboard shortcuts: `j`/`k` to move, `a` to approve, `r` to reject, `c` to focus the comment field.

**Complexity:** M

Depends on: 011, 013.

## Deliverables
- `apfun/web/routes/inbox.py`:
  - `GET /inbox` → renders list of pending candidates with trend sparkline (inline SVG from `demand_checks.autosuggest_json` or recompute), top 3 contributing signals (linked). Handler may be `async def` but uses a sync `Session` from `get_session`; query is short.
  - `POST /inbox/{candidate_id}/approve` (HTMX): writes an `approvals` row, sets candidate `decision='approved'`, enqueues a pipeline run on APScheduler (one-shot `DateTrigger(now + 1s)`), returns the updated card row (swap target).
  - `POST /inbox/{candidate_id}/reject` (HTMX): sets `decision='rejected'`, writes an `approvals` row. No pipeline queued.
  - `POST /inbox/{candidate_id}/comment` (HTMX): updates the latest `approvals` row's comment OR creates one if no decision yet (free-form "investigate angle X").
- Approval returns immediately; the actual pipeline runs in a `BackgroundScheduler` worker thread (task 019).
- Templates: `apfun/web/templates/inbox/list.html`, `apfun/web/templates/inbox/_card.html`.
- Keyboard handler in `apfun/web/static/inbox.js` (small, no framework).

## Acceptance
- Approving a card removes it from the list (swap out), writes an `approvals` row, and queues the pipeline job; the candidate's `pipeline_stage` is still `'none'` immediately after approval (the worker bumps it once it starts).
- Rejecting removes it similarly without queueing a pipeline run.
- `j`/`k`/`a`/`r`/`c` work; tested by a Playwright smoke test (opt-in).
- No flash-of-unstyled-content; the swap is instant.

## Notes
- Don't add toast notifications, modals, or animations. The bar is "feels like Vim, not Notion."
- Single user; no CSRF token needed because Apache basic auth + same-origin is the perimeter. Document this in CLAUDE.md if not already.
