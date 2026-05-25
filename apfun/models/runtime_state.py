"""`runtime_state` — a tiny key/value table for cross-restart process flags.

Currently holds exactly one flag: whether the operator has paused the scheduler
from `/ops`. APScheduler's `pause()` is in-memory only (it sets `scheduler.state`
and stops the timer; it does NOT touch the SQLAlchemyJobStore), so a fresh
`start_scheduler()` after a container restart would silently resume firing jobs.
The lifespan handler reads this flag on startup and re-applies the pause if set,
so "paused" survives a restart. Per orchestrator request 031 §1 (the minimal
persistence the spec calls for — verified APScheduler doesn't persist pause).

Deliberately generic (key/value) rather than a bespoke column so future small
process flags don't each need a migration.
"""

from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from apfun.models.base import Base, TimestampMixin

SCHEDULER_PAUSED_KEY = "scheduler_paused"


class RuntimeState(Base, TimestampMixin):
    __tablename__ = "runtime_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
