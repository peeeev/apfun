"""add unsure decision value to candidates + approvals

Extends the `Decision` enum (candidates.decision) and `ApprovalDecision`
enum (approvals.decision) with `unsure`. Both are `native_enum=False`, so the
allowed values live in a named CHECK constraint rather than a DB enum type —
the migration drops + recreates each constraint with the new value included.

SQLite has no `ALTER TABLE ... DROP CONSTRAINT`; `batch_alter_table` emulates
it by copying the table. Existing rows are unaffected (all current decision
values remain valid under the widened constraint).

Reuses the existing `approvals.comment` column as the operator-notes field
(per orchestrator request 028 — no separate `notes` column needed).

Revision ID: 7f3a9c2e1d04
Revises: 16b3688378b5
Create Date: 2026-05-23 18:30:00.000000
"""

from collections.abc import Sequence
from typing import Union

from alembic import op

revision: str = "7f3a9c2e1d04"
down_revision: Union[str, Sequence[str], None] = "16b3688378b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("candidates", schema=None) as batch_op:
        batch_op.drop_constraint("ck_candidates_decision", type_="check")
        batch_op.create_check_constraint(
            "ck_candidates_decision",
            "decision IN ('pending', 'approved', 'rejected', 'auto_killed', 'unsure')",
        )
    with op.batch_alter_table("approvals", schema=None) as batch_op:
        batch_op.drop_constraint("ck_approvals_decision", type_="check")
        batch_op.create_check_constraint(
            "ck_approvals_decision",
            "decision IN ('approve', 'reject', 'unsure')",
        )


def downgrade() -> None:
    # Note: downgrade fails if any row currently holds 'unsure' (the narrower
    # constraint would reject it). Operator must re-decide those rows first.
    with op.batch_alter_table("approvals", schema=None) as batch_op:
        batch_op.drop_constraint("ck_approvals_decision", type_="check")
        batch_op.create_check_constraint(
            "ck_approvals_decision",
            "decision IN ('approve', 'reject')",
        )
    with op.batch_alter_table("candidates", schema=None) as batch_op:
        batch_op.drop_constraint("ck_candidates_decision", type_="check")
        batch_op.create_check_constraint(
            "ck_candidates_decision",
            "decision IN ('pending', 'approved', 'rejected', 'auto_killed')",
        )
