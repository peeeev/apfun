"""add merged_into_id to candidates (merge soft-deletion)

Task 014-fix-2 (orchestrator request 031). Adds a nullable self-referential FK
`candidates.merged_into_id` — NULL = live, non-null = soft-deleted into the
referenced candidate.

`batch_alter_table` recreates `candidates` (CREATE-new → copy → DROP-old →
rename). `candidates` is referenced by `candidate_signals` + `approvals`
(ON DELETE CASCADE) and `llm_runs.candidate_id` (ON DELETE SET NULL), so the
recreate is FK-cascade-sensitive. `migrations/env.py` runs migrations with
`PRAGMA foreign_keys=OFF`, so the implicit DELETE-before-DROP can't cascade-wipe
the children — and `tests/integration/test_migration_merged_into_fk_safety.py`
proves it on a seeded DB (migration data-preservation discipline, feedback 029).

Revision ID: b2c4d6e8f0a1
Revises: 4e8f1a2b9c3d
Create Date: 2026-05-24 17:00:00.000000
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2c4d6e8f0a1"
down_revision: Union[str, Sequence[str], None] = "4e8f1a2b9c3d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("candidates", schema=None) as batch_op:
        batch_op.add_column(sa.Column("merged_into_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_candidates_merged_into_id", ["merged_into_id"])
        batch_op.create_foreign_key(
            "fk_candidates_merged_into_id",
            "candidates",
            ["merged_into_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("candidates", schema=None) as batch_op:
        batch_op.drop_index("ix_candidates_merged_into_id")
        batch_op.drop_column("merged_into_id")
