"""`dataforseo_usage` — per-call audit + budget source for the DataForSEO client.

Task 015 / orchestrator request 033. Each successful (or failed) DataForSEO call
writes one row: cost, latency, queue mode, response status, and the task_id when
running in Standard Queue mode. The monthly budget guard sums `est_cost_usd`
over the current calendar month from this table; `/ops` groups by `family` to
split SERP vs Google Ads keyword spend.

Spec Q1 (extend api_usage vs new table): chose the new table because the
existing `api_usage` is a daily *aggregate* (one row per provider/day, no
per-call columns) and the spec needs per-call fields (task_id, queue_mode,
latency_ms, status_code). Per "verify referenced affordances" — overrode the
spec's lean toward (a) once the actual shape was checked.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from apfun.models.base import Base, IdMixin, TimestampMixin


class DataForSEOUsage(Base, IdMixin, TimestampMixin):
    __tablename__ = "dataforseo_usage"
    __table_args__ = (
        # Budget queries: SUM est_cost_usd over the current month — index the
        # combo so the running sum stays cheap as the table grows.
        Index("ix_dataforseo_usage_family_created_at", "family", "created_at"),
    )

    # "serp" or "keywords_google_ads" — short, lowercase, used in /ops grouping
    # and the family filter. Endpoint paths stay in `endpoint`.
    family: Mapped[str] = mapped_column(String(40), nullable=False)
    # The DataForSEO endpoint path, e.g. "serp/google/organic/task_post" or
    # "keywords_data/google_ads/search_volume/live".
    endpoint: Mapped[str] = mapped_column(String(120), nullable=False)
    # "standard" / "priority" / "live" for SERP; NULL for keyword data (Live only).
    queue_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    est_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The Standard Queue task ID (NULL for Live mode).
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    response_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
