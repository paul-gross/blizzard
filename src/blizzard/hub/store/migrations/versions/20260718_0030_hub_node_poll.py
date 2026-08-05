"""pending-poll outcome: hub_node_poll + per-node poll cadence (issue #66, hub store tree)

Adds append-only ``hub_node_poll`` and two per-node cadence overrides; null on either takes the default.
Revision ID: 20260718_0030_hub_node_poll
Revises: 20260717_2359_hub_command_nodes
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.hub.store.schema import hub_node_poll

revision: str = "20260718_0030_hub_node_poll"
down_revision: str | None = "20260717_2359_hub_command_nodes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "graph_nodes"
_COLUMNS = ("poll_interval_seconds", "poll_timeout_seconds")


def _existing_columns(bind: sa.Connection) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    existing = _existing_columns(bind)
    for column in _COLUMNS:
        if column not in existing:
            op.add_column(_TABLE, sa.Column(column, sa.Integer(), nullable=True))
    hub_node_poll.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    hub_node_poll.drop(bind, checkfirst=True)
    existing = _existing_columns(bind)
    for column in _COLUMNS:
        if column in existing:
            op.drop_column(_TABLE, column)
