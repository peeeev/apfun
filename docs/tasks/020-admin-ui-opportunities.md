# 020 — Admin UI: opportunities

**Goal:** List + detail views for Stage 5 outputs. Sortable; dense; clickable through to source signals.

**Complexity:** M

Depends on: 018, 013.

## Deliverables
- `apfun/web/routes/opportunities.py`:
  - `GET /opportunities` — paginated list of `opportunities` joined with `candidates` + latest `scores`. Sort by `composite DESC` (default), `synthesized_at DESC`, `demand DESC`. HTMX-driven column sort.
  - `GET /opportunities/{id}` — full record: problem statement, score breakdown bars, competitors (with pricing/features/funding), top complaints with linked review excerpts, feature gaps, pricing gaps, vertical wedge, contributing raw signals.
  - `POST /opportunities/{id}/archive` — sets status=`archived` (no delete in v1).
- Templates `opportunities/list.html`, `opportunities/detail.html`, partials `_score_breakdown.html`, `_competitor_card.html`.
- Visual: small horizontal bar chart for score breakdown (inline SVG, no chart lib).

## Acceptance
- Visiting `/opportunities` with three opportunities in the DB renders them ranked by composite.
- Detail page links to source reviews and competitor URLs.
- Archive button removes from the default list view (filter `status='active'`); a `?include_archived=1` query brings them back.

## Notes
- Resist the urge to add a search box / faceted filters. If volume grows past a few hundred opportunities we'll reconsider.
