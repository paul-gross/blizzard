"""Operator-authored garden proposals (blizzard#631): `garden_proposals` gains
`origin` and `created_by`; `routine_name` becomes nullable. Existing rows backfill to
`origin='routine-run'`.

Revision ID: 20260926_1000_operator_garden_proposals
Revises: 20260923_1000_routine_lifecycle_facts
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260926_1000_operator_garden_proposals"
down_revision: str | None = "20260923_1000_routine_lifecycle_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "garden_proposals"
_ORIGIN_CHECK = "ck_garden_proposals_origin"
_ROUTINE_RUN_CHECK = "ck_garden_proposals_routine_run_has_routine"
_OPERATOR_CHECK = "ck_garden_proposals_operator_has_created_by"

# Narrow read/write stub: downgrade coalesces only this column.
_garden_proposals = sa.Table("garden_proposals", sa.MetaData(), sa.Column("routine_name", sa.String))


def upgrade() -> None:
    # SQLite has no ALTER for column nullability or a new check constraint, so this
    # table-copy recreate is load-bearing (`blizzard-context:/standards/persistence.md`).
    with op.batch_alter_table(_TABLE) as batch:
        batch.alter_column("routine_name", existing_type=sa.String(), nullable=True)
        batch.add_column(sa.Column("origin", sa.String(), nullable=False, server_default="routine-run"))
        batch.add_column(sa.Column("created_by", sa.String(), nullable=True))
        batch.create_check_constraint(_ORIGIN_CHECK, "origin IN ('routine-run', 'operator')")
        batch.create_check_constraint(_ROUTINE_RUN_CHECK, "origin != 'routine-run' OR routine_name IS NOT NULL")
        batch.create_check_constraint(_OPERATOR_CHECK, "origin != 'operator' OR created_by IS NOT NULL")


def downgrade() -> None:
    bind = op.get_bind()
    # An operator row's null `routine_name` has no home in the old `NOT NULL` shape, so
    # it is coalesced to `""` before the column narrows.
    bind.execute(_garden_proposals.update().where(_garden_proposals.c.routine_name.is_(None)).values(routine_name=""))
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint(_OPERATOR_CHECK, type_="check")
        batch.drop_constraint(_ROUTINE_RUN_CHECK, type_="check")
        batch.drop_constraint(_ORIGIN_CHECK, type_="check")
        batch.drop_column("created_by")
        batch.drop_column("origin")
        batch.alter_column("routine_name", existing_type=sa.String(), nullable=False)
