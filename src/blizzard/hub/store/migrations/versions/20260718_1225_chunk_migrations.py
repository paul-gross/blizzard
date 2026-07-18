"""cross-graph migration fact table — chunk_migrations (hub store tree, issue #90)

Phase 3 of the cross-graph-migration change: the ``chunk_migrations`` fact a
judgement choice targeting another graph records — its own recorded fact, never a
``transitions`` row (``bzh:migration-not-transition``). Adds exactly one new table
(``migration_id`` PK, ``chunk_id`` FK, ``from_node_id``, ``from_graph_id``,
``to_graph_id``, ``landed_node_id``, ``choice_name``, ``model_after``, ``epoch``,
``recorded_at``).

Brand new as of this revision — no later revision reshapes it yet — so it is imported
directly from ``schema.py`` rather than frozen locally (the exception
``20260714_0819_hub_delivery_pr_facts`` documents), the same additive-table shape
``20260718_1200_route_token_minted`` used.

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
