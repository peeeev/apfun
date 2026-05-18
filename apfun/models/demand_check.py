"""`demand_checks` — Stage 2 output (Trends + autosuggest verdict per candidate)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, Enum, Float, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from apfun.models.base import Base, IdMixin, TimestampMixin, check_enum_sql, enum_values


class DemandVerdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"


class DemandCheck(Base, IdMixin, TimestampMixin):
    __tablename__ = "demand_checks"
    __table_args__ = (
        CheckConstraint(
            check_enum_sql("verdict", DemandVerdict),
            name="ck_demand_checks_verdict",
        ),
    )

    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trend_slope: Mapped[float | None] = mapped_column(Float, nullable=True)
    autosuggest_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    verdict: Mapped[DemandVerdict] = mapped_column(
        Enum(
            DemandVerdict,
            native_enum=False,
            length=10,
            validate_strings=True,
            values_callable=enum_values,
        ),
        nullable=False,
        index=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
