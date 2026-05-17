# 014 — Admin UI inbox

**Goal:** `GET /inbox` lists Stage 2 survivors (`candidates.status = pending_review`) as cards. Each card supports approve / reject / comment without a page reload. Keyboard shortcuts: `j`/`k` to move, `a` to approve, `r` to reject, `c` to focus the comment field.

Depends on: 011, 013.

## Deliverables
- `apfun/web/routes/inbox.py`:
  - `GET /inbox` → renders list of pending candidates with trend sparkline (inline SVG from `demand_checks.autosuggest_json` or recompute), top 3 contributing signals (linked).
  - `POST /inbox/{candidate_id}/approve` (HTMX): writes an `approvals` row, sets candidate `status=approved`, returns the updated card row (swap target).
  - `POST /inbox/{candidate_id}/reject` (HTMX): same shape, sets `status=rejected`.
  - `POST /inbox/{candidate_id}/comment` (HTMX): updates the latest `approvals` row's comment OR creates one if no decision yet (free-form "investigate angle X").
- Approval is async-only at this point — it enqueues a pipeline run (task 019) but does not block the response.
- Templates: `apfun/web/templates/inbox/list.html`, `apfun/web/templates/inbox/_card.html`.
- Keyboard handler in `apfun/web/static/inbox.js` (small, no framework).

## Acceptance
- Approving a card removes it from the list (swap out) and writes both an `approvals` row and a `pipeline_runs`-equivalent task entry (task 019 will formalize this; for now, a stub function).
- Rejecting removes it similarly.
- `j`/`k`/`a`/`r`/`c` work; tested by a Playwright smoke test (opt-in).
- No flash-of-unstyled-content; the swap is instant.

## Notes
- Don't add toast notifications, modals, or animations. The bar is "feels like Vim, not Notion."
- Single user; no CSRF token needed because Apache basic auth + same-origin is the perimeter. Document this in CLAUDE.md if not already.
