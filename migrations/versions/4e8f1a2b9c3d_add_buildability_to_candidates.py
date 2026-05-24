"""add buildability columns to candidates

Task 015 (orchestrator request 030). Adds Stage 1's first *evaluation* output:

  - buildability               VARCHAR(20) NULL  (CHECK-constrained enum)
  - buildability_rationale     TEXT NOT NULL DEFAULT ''
  - buildability_assessed_at   DATETIME NULL

`buildability` is nullable (NULL = not yet assessed); the CHECK constraint
`buildability IN (...)` is satisfied by NULL because `NULL IN (...)` evaluates
to NULL, which SQLite treats as a passing constraint — no `OR ... IS NULL`
clause needed, keeping it identical to the model's `check_enum_sql` output.

`batch_alter_table` recreates `candidates` (CREATE-new → copy → DROP-old →
rename). `candidates` is referenced by `candidate_signals` and `approvals`
via `ON DELETE CASCADE`, and by `llm_runs.candidate_id` via `ON DELETE SET
NULL`, so the recreate is FK-cascade-sensitive. `migrations/env.py` runs
migrations with `PRAGMA foreign_keys=OFF`, so the implicit DELETE-before-DROP
can't cascade-wipe the children — and the data-preservation test in
`tests/integration/test_migration_buildability_fk_safety.py` proves it on a
seeded DB (per the migration data-preservation discipline, feedback 029 Q1).

Revision ID: 4e8f1a2b9c3d
Revises: 7f3a9c2e1d04
Create Date: 2026-05-24 14:00:00.000000
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "4e8f1a2b9c3d"
down_revision: Union[str, Sequence[str], None] = "7f3a9c2e1d04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BUILDABILITY_CHECK = "buildability IN ('high', 'medium', 'low', 'non_software')"


def upgrade() -> None:
    # One batch recreate: add all three columns + the CHECK constraint together.
    with op.batch_alter_table("candidates", schema=None) as batch_op:
        batch_op.add_column(sa.Column("buildability", sa.String(length=20), nullable=True))
        batch_op.add_column(
            sa.Column(
                "buildability_rationale",
                sa.Text(),
                nullable=False,
                server_default="",
            )
        )
        batch_op.add_column(
            sa.Column("buildability_assessed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_check_constraint("ck_candidates_buildability", _BUILDABILITY_CHECK)


def downgrade() -> None:
    with op.batch_alter_table("candidates", schema=None) as batch_op:
        batch_op.drop_constraint("ck_candidates_buildability", type_="check")
        batch_op.drop_column("buildability_assessed_at")
        batch_op.drop_column("buildability_rationale")
        batch_op.drop_column("buildability")
