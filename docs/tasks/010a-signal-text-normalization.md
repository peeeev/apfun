# 010a — Signal text normalization

**Goal:** Project per-source `raw_signals` rows into a uniform `signal_text` table that Stage 1 clustering and downstream stages can read without branching on `source.kind`. Per orchestrator feedback 015.

**Complexity:** M

Depends on: 002 (DB foundations), 005-009 (any source kind in `raw_signals` to normalize against).

## Why

With five sources now feeding `raw_signals` (Reddit, HN, ProductHunt, IndieHackers, review_sites with three sub-adapters), the table has heterogeneous `payload_json` shapes. Each source tags differently — Reddit uses `is_deleted`/`deletion_marker`, HN uses `_apfun_query`, PH uses `_apfun_surface`, IH uses `_apfun_group`/`_apfun_url`, reviews carry `rating`/`helpful_count`/`permalink`/`site` etc. Without normalization, every downstream consumer (clustering, scoring, synthesis) would have to know all of those shapes.

Centralizing the source-shape knowledge in one ETL stage:

- Auditability — normalization rules live in one file, reviewable independently.
- Source-agnostic clustering — task 010 reads `signal_text`, not `raw_signals`.
- Re-cluster without re-deriving — if Stage 1 changes its algorithm, the text doesn't have to be re-extracted from raw payloads.

## Deliverables

### Schema (Alembic migration)

```python
class SignalText(Base):
    __tablename__ = "signal_text"
    id: Mapped[int] = mapped_column(primary_key=True)
    raw_signal_id: Mapped[int] = mapped_column(
        ForeignKey("raw_signals.id", ondelete="CASCADE"),
        unique=True,        # idempotency — re-running updates instead of duplicating
        nullable=False,
        index=True,
    )
    source_kind: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    social_proof_weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    is_low_signal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
```

- `raw_signal_id UNIQUE` is the load-bearing detail — re-running the normalizer updates existing rows rather than inserting duplicates.
- `is_low_signal` flags rows that should not influence clustering (Reddit `[deleted]`/`[removed]`, IH `parse_error` rows, anything else the per-source extractor decides is noise).
- `social_proof_weight` is a non-negative float of raw weighted counts — **do not normalize to [0,1] here**. Stage 4 (saturation scoring) is where the right bucketing happens with full context. Per feedback 015 Q2.

### Normalizer ETL (`apfun/pipeline/normalize.py`)

ETL stage, **not a database trigger or post-insert hook** (per feedback 015 Q1: those couple ingester to clustering's needs and make schema changes harder).

```python
def normalize_raw_signals(session: Session, *, batch_size: int = 500) -> NormalizeResult:
    """Idempotent: read raw_signals → write/update signal_text rows.

    Reads only rows that lack a signal_text row OR whose signal_text was
    extracted before the raw_signal was last updated. Update-on-conflict
    rather than insert.
    """
```

Returns a `NormalizeResult(processed, inserted, updated, skipped, latency_ms)` dataclass. Writes one `scheduler_runs` row per invocation (job_id = `pipeline.normalize`).

### Per-source extractors (`apfun/pipeline/_extractors.py`)

One function per `source_kind`:

```python
def extract_reddit(payload: dict[str, Any]) -> ExtractedText
def extract_hn(payload: dict[str, Any]) -> ExtractedText
def extract_producthunt(payload: dict[str, Any]) -> ExtractedText
def extract_indiehackers(payload: dict[str, Any]) -> ExtractedText
def extract_review_sites(payload: dict[str, Any]) -> ExtractedText
```

Where `ExtractedText` is:

```python
@dataclass
class ExtractedText:
    text: str                      # combined title + body, whitespace-normalized
    social_proof_weight: float     # see weights below
    is_low_signal: bool            # e.g. Reddit [deleted]
```

Dispatch table at module scope maps `source_kind → extractor`. New sources slot in by adding a key — clean extension point.

### `social_proof_weight` formula

Initial weight map per feedback 015 Q2:

```python
# heuristic 2026-05-19 — initial weights, to be tuned via Stage 4 calibration data.
# Per CLAUDE.md retune-trigger discipline: flag for orchestrator review when
# Stage 4 (task 014) has enough llm_runs / scores rows to inform the tuning.
def social_proof_weight(source_kind: str, payload: dict[str, Any]) -> float:
    if source_kind == "reddit":
        return float(max(payload.get("score", 0), 0) + 2 * payload.get("num_comments", 0))
    if source_kind == "hn":
        return float(payload.get("points", 0) + 2 * payload.get("num_comments", 0))
    if source_kind == "producthunt":
        return float(payload.get("votesCount", 0))
    if source_kind == "indiehackers":
        # IH _NEXT_DATA__ doesn't expose a stable like/comment count yet;
        # default to 0 until we add an extraction.
        return 0.0
    if source_kind == "review_sites":
        return float(payload.get("helpful_count") or 0)
    return 0.0
```

Constants (`2 *` weights, the integer maps) carry the `# heuristic` annotation per CLAUDE.md convention.

### `text` derivation rules (per source)

- **reddit**: `title + "\n\n" + selftext`. If `is_deleted=True`, use title only and set `is_low_signal=True`.
- **hn**: stories → `title + "\n\n" + story_text`; comments → `comment_text` only (title is not posted for comment hits).
- **producthunt**: `name + "\n" + tagline + "\n\n" + description`.
- **indiehackers**: `title + "\n\n" + rawBody`. If `error_class="parse_error"` row (shouldn't happen since failed parses don't create raw_signals, but defensive), `is_low_signal=True`.
- **review_sites**: `product_name + " — " + (title or "") + "\n\n" + body`. Reviews never set `is_low_signal=True` — they're the highest-signal source by spec.

Whitespace normalization: collapse runs of whitespace, strip, ensure UTF-8.

### Idempotency

Re-running the normalizer over the same `raw_signals` set must:

- Not duplicate rows (enforced by `UNIQUE(raw_signal_id)` constraint)
- Update `extracted_at`, `text`, `social_proof_weight`, `is_low_signal` to reflect current payload
- Be safe to interrupt mid-batch (each commit is one batch, partial progress preserved)

Idempotency must have explicit test coverage (per feedback 015 action item 7).

## Acceptance

- Schema migration applies cleanly. Existing tests unaffected.
- `normalize_raw_signals(session)` against an empty `raw_signals` table is a no-op returning `NormalizeResult(processed=0, ...)`.
- `normalize_raw_signals(session)` against a populated `raw_signals` table writes one `signal_text` row per source row, with correct `source_kind`, non-empty `text`, sensible `social_proof_weight` for the source.
- Re-running `normalize_raw_signals(session)` is idempotent: zero new rows, no exceptions, `extracted_at` updated, `text`/`weight` recomputed.
- A `raw_signal` with `is_deleted=True` (Reddit) produces a `signal_text` row with `is_low_signal=True` and the title-only text.
- Per-source unit tests against captured fixtures (re-use the existing `tests/fixtures/{reddit,hn,producthunt,indiehackers,review_sites}/`) — each extractor turns a fixture payload into the expected `ExtractedText`.
- `grep -r '# TODO verify' apfun/ tests/ scripts/` returns zero at task end.

## Notes

- This task is upstream of clustering (task 010). It deliberately does NOT do any LLM work, dedup, or topic clustering — just the data-shape projection.
- `social_proof_weight` retune trigger: when Stage 4 (task 014) has ≥50 `scores` rows or a `judge()` call hits its budget warning (mirrors the `DEFAULT_THINKING_BUDGET` retune triggers from feedback 005). Open an orchestrator request rather than tuning silently.
- Future-proofing: when a new source kind is added (e.g., task X review_aggregators), add a single extractor function in `_extractors.py` + a key in the dispatch table. No other normalizer code changes.
- File layout addition (will need a one-line update to CLAUDE.md): `apfun/pipeline/normalize.py` + `apfun/pipeline/_extractors.py` are the canonical home for this stage.
