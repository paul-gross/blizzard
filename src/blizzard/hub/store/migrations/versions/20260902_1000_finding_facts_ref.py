"""finding_facts.ref — an `add` fact's own submission-local ref, null for every other
kind (blizzard#394), the same convention `note` already carries for `gone`.

Revision ID: 20260902_1000_finding_facts_ref
Revises: 20260902_0900_chunk_dependencies
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260902_1000_finding_facts_ref"
down_revision: str | None = "20260902_0900_chunk_dependencies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "finding_facts"
_COLUMN = "ref"


def _has_column(bind: sa.Connection) -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.add_column(sa.Column(_COLUMN, sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind):
        # Plain DROP COLUMN fails here post-`finding_exits` reshape (`bzh:manual-migrations`);
        # batch mode's table-copy recreate is load-bearing, not a style choice.
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.drop_column(_COLUMN)
