"""graph node session_source — targeted node-entry resume (issue #115, hub store tree)

A node's authored ``session:`` value can now name a targeted resume source
(``session: resume:<node>``), parsed into ``(SessionMode, session_source)`` by
``hub/domain/graph.py::classify_session``. ``session`` keeps its existing ``resume``/
``fresh`` column; this revision adds the parallel ``graph_nodes.session_source``
column carrying the parsed target node name — null for the bare ``resume``/``fresh``
forms, exactly mirroring how ``graph_edges`` carries the cross-graph target beside its
raw ``to_node_name`` (#90).

Nullable, no backfill (``bzh:sql-portable``): every graph minted before this column
existed reads ``session_source = NULL``, which is semantically unchanged — "resume the
chunk's most-recent session" (bare ``resume``) or "fresh" (unaffected either way). The
schema change alone flips no behavior; the runner activation is a separate, later step.

Idempotent like ``20260718_0030_hub_node_poll``: it adds the column only where an older
database created ``graph_nodes`` without it, so a fresh ``base -> head`` and an
in-place upgrade both land at exactly one column.

Revision ID: 20260721_1008_hub_graph_node_session_source
Revises: 20260720_1000_hub_chunk_intended_migration
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_1008_hub_graph_node_session_source"
down_revision: str | None = "20260721_1000_hub_escalation_decision_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "graph_nodes"
_COLUMN = "session_source"


def _has_column(bind: sa.Connection) -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind):
        op.drop_column(_TABLE, _COLUMN)
