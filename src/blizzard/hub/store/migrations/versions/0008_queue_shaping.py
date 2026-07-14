"""queue shaping — ready-queue ordering and grouping (hub store tree)

The P7W3 queue-shaping tables (design/hub/web-app.md, D-048):

* ``queue_positions`` — the append-only ready-queue ordering fact (D-004): each
  operator reorder appends the moved chunk's new float position, and order derives.
* ``chunk_grouped`` — the ``chunk.grouped`` fact (D-076/D-047): a merged-away chunk
  naming the survivor it was folded into; ephemeral, removed from every listing.

The tables are defined once in ``blizzard.hub.store.schema`` (the metadata Alembic
targets); this revision creates exactly its own subset, so a fresh ``base -> head``
and an in-place upgrade of a pre-P7W3 store both land here.

Revision ID: 0008_hub_queue_shaping
Revises: 0007_hub_gate_decisions
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import chunk_grouped, queue_positions

revision: str = "0008_hub_queue_shaping"
down_revision: str | None = "0007_hub_gate_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = [queue_positions, chunk_grouped]


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
