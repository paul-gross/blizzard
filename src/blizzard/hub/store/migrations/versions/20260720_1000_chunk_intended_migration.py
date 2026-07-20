"""chunk intended migration — a nullable, mutable ``intended_migration`` column on
chunks (issue #124, hub store tree)

A claimed chunk gains a standing intent to move onto another graph at its next
transition: ``auto`` (name-match the transition's own destination) or ``forced`` (an
unconditional named target). Consulted, never applied eagerly, by the common apply path
(``domain/apply.py``, a later phase); this revision only lands the column and the
domain/store plumbing around it — no behavior change.

A single nullable ``Text`` column carrying JSON (``{"mode", "graph_id", "node_name"}``)
rather than a fact table — the same ``bzh:facts-not-status`` shape ``graph_id``/``model``
already carry (``20260717_2318_chunk_model_selection``): a plain mutable property read
whole at consult time, with nothing filtering on its contents.

No backfill: nullable, and every chunk in flight before this column existed correctly
reads ``NULL`` — no chunk has ever had a migration intent.

Idempotent like ``20260717_2318_hub_chunk_model_selection``: it adds the column only
where an older database created ``chunks`` without it, so a fresh ``base -> head`` and
an in-place upgrade both land at exactly one column.

Revision ID: 20260720_1000_hub_chunk_intended_migration
Revises: 20260719_2000_hub_chunk_stopped_by
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260720_1000_hub_chunk_intended_migration"
down_revision: str | None = "20260719_2000_hub_chunk_stopped_by"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "chunks"
_COLUMN = "intended_migration"


def _has_column(bind: sa.Connection) -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind):
        op.drop_column(_TABLE, _COLUMN)
