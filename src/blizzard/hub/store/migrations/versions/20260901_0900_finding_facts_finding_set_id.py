"""finding_facts.finding_set_id — the delivered list a run-produced fact belongs to
(blizzard#396 D1). Nullable, no backfill: null for a person's exit verb, which belongs
to no run, and for every fact recorded before this column existed.

Revision ID: 20260901_0900_finding_facts_finding_set_id
Revises: 20260831_1100_gpc_item_index
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260901_0900_finding_facts_finding_set_id"
down_revision: str | None = "20260831_1100_gpc_item_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "finding_facts"
_COLUMN = "finding_set_id"
_FK = "fk_finding_facts_finding_set_id_finding_sets"


def _has_column(bind: sa.Connection) -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.add_column(
                sa.Column(
                    _COLUMN,
                    sa.String(),
                    sa.ForeignKey("finding_sets.finding_set_id", name=_FK),
                    nullable=True,
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind):
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.drop_column(_COLUMN)
