"""`candidates` (Stage 1 output) and the `candidate_signals` junction.

`decision` tracks the HITL outcome (owned by the inbox endpoint, task 014).
`pipeline_stage` tracks machine progress (owned by the Stage 3→5 orchestrator,
task 019). They evolve independently — see CLAUDE.md → Lessons learned.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import JSON, CheckConstraint, Enum, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from apfun.models.base import Base, IdMixin, TimestampMixin, check_enum_sql, enum_values


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


class Candidate(Base, IdMixin, TimestampMixin):
    __tablename__ = "candidates"
    __table_args__ = (
        CheckConstraint(check_enum_sql("decision", Decision), name="ck_candidates_decision"),
        CheckConstraint(
            check_enum_sql("pipeline_stage", PipelineStage),
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
            values_callable=enum_values,
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
            values_callable=enum_values,
        ),
        default=PipelineStage.NONE,
        nullable=False,
        index=True,
    )


class CandidateSignal(Base):
    __tablename__ = "candidate_signals"
    __table_args__ = (
        # Composite PK indexes (candidate_id, raw_signal_id) left-prefix only.
        # Add a standalone index for the reverse-direction join.
        Index("ix_candidate_signals_raw_signal_id", "raw_signal_id"),
    )

    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), primary_key=True
    )
    raw_signal_id: Mapped[int] = mapped_column(
        ForeignKey("raw_signals.id", ondelete="CASCADE"), primary_key=True
    )
