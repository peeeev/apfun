"""add dataforseo_usage table (per-call audit + budget source)

Task 015 / orchestrator request 033. CREATE TABLE for the per-call DataForSEO
record. New table, no FKs, no children — no cascade risk; nothing pre-existing
to preserve.

Revision ID: d4e6f8a0b2c5
Revises: c3d5e7f9a1b2
Create Date: 2026-05-30 12:00:00.000000
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4e6f8a0b2c5"
down_revision: Union[str, Sequence[str], None] = "c3d5e7f9a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dataforseo_usage",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("family", sa.String(length=40), nullable=False),
        sa.Column("endpoint", sa.String(length=120), nullable=False),
        sa.Column("queue_mode", sa.String(length=16), nullable=True),
        sa.Column("est_cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("response_size_bytes", sa.Integer(), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_dataforseo_usage_family_created_at",
        "dataforseo_usage",
        ["family", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_dataforseo_usage_family_created_at", table_name="dataforseo_usage")
    op.drop_table("dataforseo_usage")
