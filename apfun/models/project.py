"""`projects` — opportunities that progressed to a built subdomain."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import CheckConstraint, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from apfun.models.base import Base, IdMixin, TimestampMixin, check_enum_sql, enum_values


class ProjectStatus(StrEnum):
    PLACEHOLDER = "placeholder"
    IN_DEV = "in_dev"
    LIVE = "live"
    SUNSET = "sunset"


class Project(Base, IdMixin, TimestampMixin):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint(
            check_enum_sql("status", ProjectStatus),
            name="ck_projects_status",
        ),
    )

    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    subdomain: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(
            ProjectStatus,
            native_enum=False,
            length=20,
            validate_strings=True,
            values_callable=enum_values,
        ),
        default=ProjectStatus.PLACEHOLDER,
        nullable=False,
        index=True,
    )
