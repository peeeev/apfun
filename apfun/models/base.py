"""SQLAlchemy declarative base, shared mixins, and enum helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class IdMixin:
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )


def enum_values(enum_cls: type[StrEnum]) -> list[str]:
    """Lowercase string values for a StrEnum (used as SQLAlchemy `Enum.values_callable`)."""
    return [m.value for m in enum_cls]


def check_enum_sql(column: str, enum_cls: type[StrEnum]) -> str:
    """SQL fragment for a `CheckConstraint` enforcing enum values on a column."""
    values = ", ".join(f"'{v}'" for v in enum_values(enum_cls))
    return f"{column} IN ({values})"
