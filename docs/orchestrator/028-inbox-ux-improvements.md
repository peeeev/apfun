# Request 028: task 014-fix-1 — inbox UX improvements

**Date:** 2026-05-23

**Context.** First real triage session (operator using `/inbox` against runbook 001 candidates) surfaced concrete UX friction. The friction patterns are pure observation, not speculation — they're exactly the points where the operator wanted to do something and the UI didn't support it.

This PR bundles the addressable friction into one task, plus a small CLAUDE.md convention update that's similarly small.

## Goal

Make `/inbox` a complete triage workspace, not just a listing. Specifically:

1. The listing shows enough source context to triage without leaving the page in most cases.
2. A detail view at `/inbox/<id>` shows the contributing signals with their original text + source URLs for cases where the listing isn't enough.
3. The decision states extend from binary (approve/reject) to ternary (approve/reject/unsure) with an associated notes field.
4. Status-filtered views let the operator find previously-decided candidates (approved/rejected/unsure) when needed.

## Scope

**In scope — schema:**

- New value `unsure` added to the `Candidate.decision` enum (or column, depending on shape). Migration via Alembic.
- New `notes` column on `approvals` table (or wherever the decision-with-context belongs). `notes TEXT NOT NULL DEFAULT ''`. Stores operator's rationale for the decision; visible in detail view; editable.

**In scope — listing (`/inbox`):**

- Each candidate row shows source context inline. Concrete shape: small badges/tags per source contributing to the cluster. Format examples:
  - Reddit: `r/SaaS`, `r/Entrepreneur` (one badge per subreddit contributing)
  - HN: `hn:wishes` (the query bundle name)
  - ProductHunt: `ph:topics`, `ph:leaderboard` (surface name)
  - IndieHackers: `ih:starting-up` (group name)
  - Review sites: `g2:asana`, `capterra:asana`
- Multiple sources per candidate render as multiple badges side-by-side. If 5+ sources, show first 3 + "+N more" affordance.
- Existing approve/reject buttons stay. Add new "Unsure" button next to them.
- Decision buttons accept a notes textarea inline (small, expandable). On submit, the notes go into `approvals.notes`.

**In scope — detail view (`/inbox/<id>`):**

- New route. GET-only initially.
- Shows all listing-page fields (problem_statement, suspected_user, seed_keywords, composite weight, source badges).
- Shows the contributing signals: for each `candidate_signal`, render a card with:
  - `signal_text.text` (truncated to ~500 chars, "show more" affordance for full text)
  - `source_kind` and `source_identifier` (subreddit name, HN query, etc.)
  - `raw_signal.url` as a clickable link to the original post
  - `signal_text.social_proof_weight`
  - `raw_signal.captured_at` (relative-time formatted)
- Approve/Reject/Unsure buttons present here too, with notes textarea. Submitting from detail view does the same thing as submitting from listing.
- Back-link to `/inbox`.

**In scope — status-filtered views:**

- `/inbox` continues to show only `decision='pending'` (current behavior).
- New routes:
  - `/inbox/approved` — candidates where `decision='approved'`
  - `/inbox/rejected` — candidates where `decision='rejected'`
  - `/inbox/unsure` — candidates where `decision='unsure'`
- Each is the same template as `/inbox` (listing layout) but with the appropriate filter.
- Nav links between them at the top of each page.

**In scope — CLAUDE.md convention bundle:**

- Add a Lesson Learned entry (or short Convention; either's fine) documenting the orchestrator-hallucination pattern surfaced today:

  > **Verify referenced affordances before assuming they exist.** The orchestrator describes desired/expected shapes that may not match implemented shapes. When an instruction says "click here," "look at this field," or otherwise references UI/CLI affordances that haven't been verified against the current codebase, the operator should sanity-check (browser, grep, "actually I don't see that" pushback) rather than assuming the mental model matches. Same discipline applies to module paths, env-var names, file paths — anything specific to *this* codebase. The orchestrator commits to verifying before describing; the operator commits to checking before trusting.

**Out of scope:**

- Auto-pre-fill notes (e.g., "Rejected: payment processing — N other similar rejected"). Future feature.
- Bulk decisions ("approve all from r/SaaS"). Future feature.
- Search/filter within a list. Future feature.
- Operator-blocklist for topics like payment processing. Future feature; reject manually for now.
- Editing decisions after the fact ("I rejected this but want to undo"). Useful but not in this PR. Note in PR description for future.

## Implementation notes

**Schema migration** is small but real. The decision enum currently is probably `('pending', 'approved', 'rejected')` — extending requires either an Alembic migration with `ALTER TYPE ... ADD VALUE 'unsure'` (Postgres) or a column-replace strategy (SQLite). SQLite's enum-by-CHECK constraint approach means likely a CHECK constraint update. Implementer adapts to the actual shape.

**Source identifier extraction** mirrors what's needed in request 027's `_extract_source_identifier()`. **Worth extracting to a shared helper** since both tasks need it. Suggestion: `apfun/pipeline/_source_identifier.py` or a method on the `RawSignal` model. Don't duplicate the logic in two places.

**Notes field UI.** The friction pattern is "I want to record why I made this decision." The simplest UI: textarea expands on focus, persists on submit. HTMX-friendly. Empty notes are fine — don't require text to submit a decision.

**"Unsure" semantic.** Unsure is *not* the same as pending. Pending = "the operator hasn't seen this yet." Unsure = "the operator looked and couldn't decide." Both should be re-reviewable later, but they're conceptually different. Don't lump them.

**Status-filter routes — DRY.** All four routes (`/inbox`, `/inbox/approved`, `/inbox/rejected`, `/inbox/unsure`) share template logic. Implement as one parameterized function with a filter dispatch. Don't copy-paste four near-identical view functions.

## Tests

Substantial test surface for an M-complexity PR:

- Schema migration test: `unsure` is a valid `decision` value; existing rows unaffected.
- Migration test: `approvals.notes` exists, defaults to empty string.
- Listing test: source badges render correctly for each source kind.
- Listing test: candidates with 5+ source contributions render "first 3 + N more".
- Detail view test: GET `/inbox/<id>` returns 200 for pending candidate; returns 404 for nonexistent id.
- Detail view test: contributing signals render with text + URLs.
- Decision POST tests (approve/reject/unsure): persist correctly, notes saved, response redirects/refreshes properly.
- Status-filter tests: `/inbox/approved` shows only approved; `/inbox/rejected` shows only rejected; `/inbox/unsure` shows only unsure.
- HITL durability test (carried over from feedback 016 Q5): submitting `approve` on a previously-rejected candidate that's accumulated new signals does NOT auto-flip; the "re-review?" UI element appears appropriately.

## Documentation updates (same PR per the docs-update convention)

1. **`CLAUDE.md → Lessons Learned`** — the hallucination convention text from above.
2. **`docs/operator/SETUP.md`** — document the new routes (`/inbox/approved`, `/inbox/rejected`, `/inbox/unsure`, `/inbox/<id>`).
3. **`docs/tasks/014-inbox-endpoint.md`** — footer note: "Extended in task 014-fix-1 with detail view, Unsure state, notes field, source visibility, status-filtered views. See `014-fix-1-inbox-ux.md`."
4. **`docs/tasks/014-fix-1-inbox-ux.md`** — new task file.
5. **`docs/orchestrator/INDEX.md`** — row 028 → answered after PR merges.

## What I would do next without intervention

1. Branch `feature/task-014-fix-1-inbox-ux`.
2. Alembic migration for `unsure` decision value + `notes` column. Verify against existing data (no row should fail validation).
3. Shared `_extract_source_identifier()` helper (per-source dispatch). Same shape that runbook 004's dump script needs — if 027 and 028 ship in parallel, the helper lives in one of them and the other imports.
4. Update inbox listing template to include source badges and Unsure button.
5. New `/inbox/<id>` route + template.
6. New status-filter routes + shared parameterized view function.
7. Notes textarea + POST handler updates.
8. Tests per the Tests section.
9. CLAUDE.md Lessons Learned entry.
10. Update INDEX, task 014 footer, new task file.
11. Open PR. Verify in browser:
    - Listing shows source badges
    - Detail view shows contributing signals with URLs
    - Approve/Reject/Unsure all work; notes save
    - `/inbox/approved` etc. filter correctly
    - HITL durability still holds (rejected candidates with new signals show re-review prompt)

## Specific questions or risks

1. **Are decisions reversible?** Currently no — once approved/rejected, that's it. With Unsure as a tertiary state, the natural operator question becomes "can I move an Unsure to Approved later?" Two options: (a) status-filtered views show buttons (any candidate can be re-decided); (b) once-decided-stays-decided, Unsure included. **My lean: (a) — any candidate can be re-decided, including approved/rejected ones.** This serves the operator-iteration workflow. HITL durability (feedback 016 Q5) doesn't preclude this — that rule is about *auto*-flipping based on new evidence, not about *operator* re-decisions. Worth documenting the distinction.

2. **Where do notes live: per-candidate or per-decision?** A candidate could have multiple decisions over time (pending → rejected → operator changes mind → approved). Notes per *decision* (i.e., on the `approvals` table) preserves the history. Notes per *candidate* loses that. **My lean: per-decision (on approvals).** Each row in `approvals` is one operator action; notes belong with the action. The detail view shows the most recent notes plus a "decision history" if more than one exists.

3. **Source badge interaction.** Should clicking a source badge filter the inbox to that source? E.g., click `r/SaaS` → show only candidates contributed-to by r/SaaS. **My lean: no, not yet.** Future feature. Don't bake in click handlers; static badges only.

4. **Notes editing after submit.** If the operator approves with a note, then comes back later and wants to update the note, should they be able to? **My lean: yes, but as a "new decision" (new approvals row) rather than editing the previous one.** Same decision value (`approved`), updated notes. Preserves history. Implementer can use judgment if this becomes too complex.

5. **Empty inbox state for status-filtered views.** `/inbox/approved` when nothing's approved yet: empty-state message ("No approved candidates yet"). Same pattern as the empty-state for `/inbox` itself. Cheap to do; high payoff in UX.

## Relevant files

Code under change:
- `apfun/models/candidate.py` (or wherever the decision enum lives) — extend
- `apfun/models/approval.py` — add notes column
- `apfun/pipeline/_source_identifier.py` (or similar) — new shared helper
- Alembic migration file — new
- `apfun/web/routes/inbox.py` — add detail view, status-filter routes, Unsure handler
- `apfun/web/templates/inbox/listing.html` (or wherever) — source badges, Unsure button, notes textarea
- `apfun/web/templates/inbox/detail.html` — new
- `apfun/web/templates/_base.html` — nav links to filter views (optional but nice)
- `tests/unit/test_inbox_route.py` — extend
- `tests/unit/test_inbox_detail.py` — new

Docs under change:
- `CLAUDE.md` — Lessons Learned entry
- `docs/operator/SETUP.md` — new routes
- `docs/tasks/014-inbox-endpoint.md` — footer
- `docs/tasks/014-fix-1-inbox-ux.md` — new task file
- `docs/orchestrator/INDEX.md` — row 028 → answered

## Meta note — UX feedback compounding

This PR addresses six discrete friction points from one triage session. That's a lot of value per turn. Worth noting: **the orchestrator pattern is now in observation-driven mode, not pre-spec mode.** Feature requests are coming from the operator's lived experience rather than from upfront design. That's the right posture for a personal-use project; trust it.

The natural follow-on after this PR: another triage session against the better UX, which will surface a new round of friction. Some of those will be feature requests (next round of UI work); some will be Stage 1 prompt issues (depending on what 027 surfaces). The cadence to settle into: triage → orchestrator → ship → triage. Each cycle teaches what to build next.

Don't try to anticipate what the next round will need. The operator-experience signal is more valuable than orchestrator pre-thinking.
