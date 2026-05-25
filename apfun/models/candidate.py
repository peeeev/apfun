"""`candidates` (Stage 1 output) and the `candidate_signals` junction.

`decision` tracks the HITL outcome (owned by the inbox endpoint, task 014).
`pipeline_stage` tracks machine progress (owned by the Stage 3→5 orchestrator,
task 019). They evolve independently — see CLAUDE.md → Lessons learned.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import JSON, CheckConstraint, DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from apfun.models.base import Base, IdMixin, TimestampMixin, check_enum_sql, enum_values


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Decision(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    AUTO_KILLED = "auto_killed"
    # UNSURE ≠ PENDING. PENDING = operator hasn't looked yet; UNSURE = looked
    # and couldn't decide. Both are re-reviewable but conceptually distinct.
    # Per orchestrator request 028. The operator may re-decide any candidate
    # (an explicit re-decision — distinct from the auto-flip HITL durability
    # forbids).
    UNSURE = "unsure"


class PipelineStage(StrEnum):
    NONE = "none"
    COMPETITIVE = "competitive"
    SCORING = "scoring"
    SYNTHESIZING = "synthesizing"
    DONE = "done"
    FAILED = "failed"


class Buildability(StrEnum):
    """Stage 1's first *evaluation* judgment: is the complaint software-addressable?

    Set at cluster time for new candidates (the `cluster.j2` Opus call now emits
    it alongside the cluster) and backfilled once for pre-existing candidates via
    `scripts/backfill_buildability.py`. It's a *hint*, never a gate — the operator
    can approve a `non_software` candidate anyway, and buildability does NOT feed
    the composite weight (which stays social-proof-only). Per orchestrator
    request 030 (task 015).
    """

    HIGH = "high"  # clearly software-addressable; a founder could start next week
    MEDIUM = "medium"  # partly software-addressable; needs non-software complements
    LOW = "low"  # software is a minor part; needs judgment/regulation/capital/scale
    NON_SOFTWARE = "non_software"  # cultural/regulatory/philosophical — not a product


class Candidate(Base, IdMixin, TimestampMixin):
    __tablename__ = "candidates"
    __table_args__ = (
        CheckConstraint(check_enum_sql("decision", Decision), name="ck_candidates_decision"),
        CheckConstraint(
            check_enum_sql("pipeline_stage", PipelineStage),
            name="ck_candidates_pipeline_stage",
        ),
        # Nullable: a NULL `buildability` (not yet assessed) satisfies the CHECK
        # because `NULL IN (...)` evaluates to NULL, which SQLite treats as a
        # passing constraint. No `OR ... IS NULL` clause needed — keeps this
        # identical to the migration's `check_enum_sql` output.
        CheckConstraint(
            check_enum_sql("buildability", Buildability),
            name="ck_candidates_buildability",
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
    # Buildability (task 015). NULL = not yet assessed (rare after the one-time
    # backfill; new candidates always get a value at cluster time). Not indexed:
    # 4-value low-cardinality column on a small table, and the inbox filter is a
    # cheap in-Python exclusion, not a hot query path.
    buildability: Mapped[Buildability | None] = mapped_column(
        Enum(
            Buildability,
            native_enum=False,
            length=20,
            validate_strings=True,
            values_callable=enum_values,
        ),
        nullable=True,
    )
    buildability_rationale: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="", default=""
    )
    buildability_assessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Merge soft-deletion (task 014-fix-2). NULL = live; non-null = this candidate
    # was merged into the referenced one and is soft-deleted (excluded from every
    # listing via `WHERE merged_into_id IS NULL`). Self-referential FK; indexed
    # per the explicit-FK-index convention. ON DELETE SET NULL so a hard-delete of
    # the parent (not something we do — merges are soft) wouldn't orphan the ref.
    # Per orchestrator request 031.
    merged_into_id: Mapped[int | None] = mapped_column(
        ForeignKey("candidates.id", ondelete="SET NULL", name="fk_candidates_merged_into_id"),
        nullable=True,
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
    # Per orchestrator feedback 016 Q5/Q8: enables "N signals since rejection"
    # UI computation and supports manual re-cluster (operator deletes rows;
    # next Stage 1 run treats them as unclustered).
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
