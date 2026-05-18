"""`competitive_analyses` — Stage 3 output, one row per (candidate, competitor)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from apfun.models.base import Base, IdMixin, TimestampMixin


class CompetitiveAnalysis(Base, IdMixin, TimestampMixin):
    __tablename__ = "competitive_analyses"

    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    competitor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    competitor_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    pricing_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    features_json: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    funding_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    reviews_summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
