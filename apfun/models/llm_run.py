"""`llm_runs` — every Anthropic API call (audit + cost).

Logged by the single LLM entrypoint at `apfun/llm/client.py` (task 004).
`task` is a short tag ("cluster", "dedup", "synthesize", ...) used to filter
the audit log and to enforce the model-selection policy.
`attempts` records how many SDK calls the wrapper made before settling — 1 if
the first call succeeded; up to `_MAX_RETRIES` on transient failures.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from apfun.models.base import Base, IdMixin, TimestampMixin


class LLMRun(Base, IdMixin, TimestampMixin):
    __tablename__ = "llm_runs"
    __table_args__ = (
        # Audit queries are nearly always "calls of task X over a time window".
        Index("ix_llm_runs_task_created_at", "task", "created_at"),
    )

    task: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    est_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("candidates.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
