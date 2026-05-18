"""`opportunities` — Stage 5 differentiation synthesis output (one per candidate)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, Enum, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from apfun.models.base import Base, IdMixin, TimestampMixin, check_enum_sql, enum_values


class OpportunityStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    BUILT = "built"


class Opportunity(Base, IdMixin, TimestampMixin):
    __tablename__ = "opportunities"
    __table_args__ = (
        CheckConstraint(
            check_enum_sql("status", OpportunityStatus),
            name="ck_opportunities_status",
        ),
    )

    # UNIQUE creates the index — no separate `index=True` needed.
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    top_complaints_json: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    feature_gaps_json: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    pricing_gaps_json: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    vertical_wedge: Mapped[str | None] = mapped_column(Text, nullable=True)
    sources_json: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    synthesized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[OpportunityStatus] = mapped_column(
        Enum(
            OpportunityStatus,
            native_enum=False,
            length=20,
            validate_strings=True,
            values_callable=enum_values,
        ),
        default=OpportunityStatus.ACTIVE,
        nullable=False,
        index=True,
    )
