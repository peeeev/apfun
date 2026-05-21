"""`signal_text` — uniform projection of per-source `raw_signals` for Stage 1.

Per task 010a + orchestrator feedback 015 Q1: source-shape knowledge lives in
the normalization ETL (`apfun.pipeline.normalize`), not in clustering or any
downstream stage. `signal_text` is the read-shape downstream stages consume.

`raw_signal_id` is UNIQUE — re-running the normalizer updates the existing
row rather than inserting duplicates. `is_low_signal` flags rows clustering
should not weight (Reddit `[deleted]`/`[removed]`, anything else the extractors
mark as noise).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from apfun.models.base import Base, IdMixin


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SignalText(Base, IdMixin):
    __tablename__ = "signal_text"
    __table_args__ = (Index("ix_signal_text_source_kind", "source_kind"),)

    raw_signal_id: Mapped[int] = mapped_column(
        ForeignKey("raw_signals.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    source_kind: Mapped[str] = mapped_column(String(50), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # heuristic 2026-05-19 — raw weighted counts; do NOT normalize to [0,1]
    # here. Stage 4 (task 014) is where bucketing happens with full context.
    # Per orchestrator feedback 015 Q2.
    social_proof_weight: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0.0"
    )
    is_low_signal: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
