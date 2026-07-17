"""graph node produces + checks (hub store tree)

Adds the ``graph_nodes.produces`` and ``graph_nodes.checks`` columns (P7): each a
JSON-encoded ``list[str]`` round-tripping a node's authored ``produces`` (the artifact
names it emits — the review node's ``review-findings`` asset) and ``checks`` (the
deterministic commands the worker runs in-session). Before this, the walking
skeleton's graph store dropped both on mint and reified them as ``[]``, so a review node
reloaded from the store carried no ``produces`` — and the runner therefore never emitted
the findings asset a review *fail* is meant to carry back into build. Persisting them
makes the review node's findings a real artifact on the live rails.

The hub store's Alembic tree targets one shared ``schema`` metadata whose table objects
reflect the *current* definition, so a fresh database's 0002 already creates
``graph_nodes`` **with** these columns. This revision is therefore written **idempotent**
— it adds each column only where an older database created ``graph_nodes`` without it —
so ``base -> head`` on a fresh store and an in-place upgrade of a pre-P7 store both land
at exactly one of each column.

Revision ID: 20260713_1716_hub_graph_node_produces_checks
Revises: 20260713_1635_hub_runner_high_water
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_1716_hub_graph_node_produces_checks"
down_revision: str | None = "20260713_1635_hub_runner_high_water"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "graph_nodes"
_COLUMNS = ("produces", "checks")


def _existing(bind: sa.Connection) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    present = _existing(bind)
    for column in _COLUMNS:
        if column not in present:
            op.add_column(_TABLE, sa.Column(column, sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    present = _existing(bind)
    for column in _COLUMNS:
        if column in present:
            op.drop_column(_TABLE, column)
