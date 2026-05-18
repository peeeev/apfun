"""`api_usage` — daily aggregate of external API spend (DataForSEO budget cap, etc.)."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import JSON, Date, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from apfun.models.base import Base, IdMixin, TimestampMixin


class ApiUsage(Base, IdMixin, TimestampMixin):
    __tablename__ = "api_usage"
    __table_args__ = (
        # One row per (provider, day); the upsert key.
        UniqueConstraint("provider", "day", name="uq_api_usage_provider_day"),
    )

    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    est_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
