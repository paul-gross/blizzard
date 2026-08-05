"""cross-graph migration fact table — chunk_migrations (hub store tree, issue #90). One
new table, imported from ``schema.py``: no later revision reshapes it yet.

Revision ID: 20260718_1225_hub_chunk_migrations
Revises: 20260718_1220_hub_edge_target_graph_model
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import chunk_migrations

revision: str = "20260718_1225_hub_chunk_migrations"
down_revision: str | None = "20260718_1220_hub_edge_target_graph_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    chunk_migrations.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    chunk_migrations.drop(bind, checkfirst=True)
