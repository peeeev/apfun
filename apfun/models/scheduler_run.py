"""`scheduler_runs` — one row per APScheduler job firing (sources health UI)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from apfun.models.base import Base, IdMixin, TimestampMixin


class SchedulerRun(Base, IdMixin, TimestampMixin):
    __tablename__ = "scheduler_runs"
    __table_args__ = (
        # "Last run of job X" + "recent failures by job" queries.
        Index("ix_scheduler_runs_job_id_started_at", "job_id", "started_at"),
    )

    job_id: Mapped[str] = mapped_column(String(100), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    items_processed: Mapped[int | None] = mapped_column(Integer, nullable=True)
