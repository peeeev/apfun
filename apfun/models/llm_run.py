"""`llm_runs` — every Anthropic API call (audit + cost).

Logged by the single LLM entrypoint at `apfun/llm/client.py` (task 004).
`task` is a short tag ("cluster", "dedup", "synthesize", ...) used to filter
the audit log and to enforce the model-selection policy.
`attempts` records how many SDK calls the wrapper made before settling — 1 if
the first call succeeded; up to `_MAX_RETRIES` on transient failures.
`retry_log_json` captures per-attempt error details for attempts BEFORE the
final one (the final attempt's outcome lives in `ok`, `error`, `latency_ms`).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Index, Integer, String, Text, text
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
    retry_log_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list, server_default=text("'[]'")
    )
    candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("candidates.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
