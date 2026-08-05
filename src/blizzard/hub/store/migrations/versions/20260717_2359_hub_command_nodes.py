"""generic hub command nodes (issue #65): ``graph_nodes.run``, the JSON command list null on every other
node, and ``hub_exec_slot``, the fleet-wide serialization lease as a FACT (``bzh:facts-not-status``).

Revision ID: 20260717_2359_hub_command_nodes
Revises: 20260717_2345_hub_chunk_bounces
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.hub.store.schema import hub_exec_slot

revision: str = "20260717_2359_hub_command_nodes"
down_revision: str | None = "20260717_2345_hub_chunk_bounces"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "graph_nodes"
_COLUMN = "run"


def _has_column(bind: sa.Connection) -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Text(), nullable=True))
    hub_exec_slot.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    hub_exec_slot.drop(bind, checkfirst=True)
    if _has_column(bind):
        op.drop_column(_TABLE, _COLUMN)
