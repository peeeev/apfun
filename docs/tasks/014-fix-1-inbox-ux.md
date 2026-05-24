# 014-fix-1 — inbox UX improvements

**Goal:** make `/inbox` a complete triage workspace, not just a listing. Bundles the addressable friction from the first real triage session into one PR.

**Complexity:** M

Depends on: 014 (inbox endpoint). Per orchestrator request 028.

## Deliverables

- **Schema:** `unsure` added to `Decision` (candidate status) and `ApprovalDecision` (operator action) enums + their CHECK constraints. Alembic migration `7f3a9c2e1d04` (batch mode — SQLite recreates the table). Operator notes reuse the existing `approvals.comment` column (no separate `notes` column — `comment` already serves the purpose).
- **Listing:** per-source badges (`r/SaaS`, `hn:wishes`, `ph:topics`, `ih:<group>`, `<site>:<slug>`), collapsing to "first 3 + N more". Approve / reject / **unsure** buttons + an optional notes textarea per card (`hx-include="closest article"` carries the notes with whichever button fires).
- **Detail view:** `GET /inbox/<id>` — candidate header (reuses the card) + every contributing signal with text, source label, original-post URL, weight, relative captured-at; plus decision history. 404 on unknown id. Declared with the `:int` converter *before* the string-param filter route so integers route to detail and `approved`/etc. fall through.
- **Status-filtered listings:** `/inbox/approved`, `/inbox/rejected`, `/inbox/unsure` — one parameterized view (`_FILTERS` dispatch), not four copies. Filter nav at the top of every listing. Empty-states per filter.
- **Shared helper:** `apfun/pipeline/_source_identifier.py` — per-source-kind dispatch from `payload_json`. (Runbook 004's diagnostic script carries its own inline copy to stay an independent one-time script.)

## Acceptance

- Listing shows source badges; 5+ distinct sources collapse to "first 3 + N more".
- `/inbox/<id>` returns 200 with signals + URLs for a real candidate; 404 for unknown.
- approve / reject / unsure each persist an `approvals` row (with notes) and flip `candidates.decision` accordingly.
- `/inbox/approved|rejected|unsure` show only matching candidates; empty-states otherwise; unknown filter → 404.
- Any candidate is re-decidable (explicit operator action). HITL durability still holds: new signals after rejection surface a re-review prompt but never auto-flip.

## Notes

- **Unsure ≠ pending.** Pending = operator hasn't looked; unsure = looked, couldn't decide. Both re-reviewable, conceptually distinct.
- **Re-decision vs auto-flip.** Operator re-decisions are explicit and always allowed (status-filter views show decision controls). The HITL-durability rule (feedback 016 Q5) forbids *auto*-flipping on new evidence — a different thing.
- Out of scope (future, on observed friction): auto-prefilled notes, bulk decisions, in-list search, operator topic-blocklist, badge-click filtering.
