"""generic hub command nodes: graph_nodes.run + the hub_exec_slot lease (issue #65)

The hub command node primitive (#65): a node whose YAML declares ``executor: hub`` plus
a ``run:`` list of shell commands the hub executes itself, serialized fleet-wide, with
no agent. This revision adds:

* ``graph_nodes.run`` — the JSON-encoded ``list[{command, name, produces}]`` a generic
  hub command node authors; null on every other node (including the still-special
  deliver node, which carries no ``run:`` until #67).
* ``hub_exec_slot`` — the fleet-wide serialization lease: a FACT (``bzh:facts-not-status``),
  not an in-process lock, so a crash leaves a derivable, reclaimable trace and the
  invariant checker can assert at most one live slot. Brand new as of this revision, so
  imported directly from ``schema.py`` (the same exception ``chunk_bounces``'s own
  migration documents).

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
