"""`scores` — Stage 4 saturation scoring output (one row per scoring run)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from apfun.models.base import Base, IdMixin, TimestampMixin


class Score(Base, IdMixin, TimestampMixin):
    __tablename__ = "scores"

    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    demand: Mapped[float] = mapped_column(Float, nullable=False)
    supply: Mapped[float] = mapped_column(Float, nullable=False)
    unmet_pain: Mapped[float] = mapped_column(Float, nullable=False)
    moat_potential: Mapped[float] = mapped_column(Float, nullable=False)
    composite: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    breakdown_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    model_version: Mapped[str] = mapped_column(String(50), nullable=False)
