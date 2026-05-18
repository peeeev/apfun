"""`approvals` — HITL inbox decisions (one row per approve/reject; comments may update)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from apfun.models.base import Base, IdMixin, TimestampMixin, check_enum_sql, enum_values


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class Approval(Base, IdMixin, TimestampMixin):
    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint(
            check_enum_sql("decision", ApprovalDecision),
            name="ck_approvals_decision",
        ),
    )

    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    decision: Mapped[ApprovalDecision] = mapped_column(
        Enum(
            ApprovalDecision,
            native_enum=False,
            length=10,
            validate_strings=True,
            values_callable=enum_values,
        ),
        nullable=False,
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
