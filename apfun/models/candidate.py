"""`candidates` (Stage 1 output) and the `candidate_signals` junction.

The `decision` column tracks the HITL outcome (owned by the inbox endpoint, task 014);
`pipeline_stage` tracks machine progress (owned by the Stage 3→5 orchestrator, task 019).
They evolve independently — see CLAUDE.md → Lessons learned.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import JSON, CheckConstraint, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from apfun.models.base import Base, IdMixin, TimestampMixin


class Decision(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    AUTO_KILLED = "auto_killed"


class PipelineStage(StrEnum):
    NONE = "none"
    COMPETITIVE = "competitive"
    SCORING = "scoring"
    SYNTHESIZING = "synthesizing"
    DONE = "done"
    FAILED = "failed"


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    """Return the lowercase string values for a StrEnum (used by SQLAlchemy `Enum`)."""
    return [m.value for m in enum_cls]


_DECISION_VALUES = ", ".join(f"'{v}'" for v in _enum_values(Decision))
_PIPELINE_STAGE_VALUES = ", ".join(f"'{v}'" for v in _enum_values(PipelineStage))


class Candidate(Base, IdMixin, TimestampMixin):
    __tablename__ = "candidates"
    __table_args__ = (
        CheckConstraint(
            f"decision IN ({_DECISION_VALUES})",
            name="ck_candidates_decision",
        ),
        CheckConstraint(
            f"pipeline_stage IN ({_PIPELINE_STAGE_VALUES})",
            name="ck_candidates_pipeline_stage",
        ),
    )

    problem_statement: Mapped[str] = mapped_column(Text, nullable=False)
    suspected_user: Mapped[str | None] = mapped_column(Text, nullable=True)
    seed_keywords_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    vertical: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    dedup_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    decision: Mapped[Decision] = mapped_column(
        Enum(
            Decision,
            native_enum=False,
            length=20,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        default=Decision.PENDING,
        nullable=False,
        index=True,
    )
    pipeline_stage: Mapped[PipelineStage] = mapped_column(
        Enum(
            PipelineStage,
            native_enum=False,
            length=20,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        default=PipelineStage.NONE,
        nullable=False,
        index=True,
    )


class CandidateSignal(Base):
    __tablename__ = "candidate_signals"

    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), primary_key=True
    )
    raw_signal_id: Mapped[int] = mapped_column(
        ForeignKey("raw_signals.id", ondelete="CASCADE"), primary_key=True
    )
