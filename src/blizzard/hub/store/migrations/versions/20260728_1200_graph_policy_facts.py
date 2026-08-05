"""graph policy facts — the per-graph follow-latest tri-state (hub store tree)

Append-only, newest-fact-wins, ``checkfirst``; ``follow_latest`` is nullable, and NULL means inherit.
Revision ID: 20260728_1200_hub_graph_policy_facts
Revises: 20260726_1200_hub_chunk_work_refs_rename
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import graph_policy_facts

revision: str = "20260728_1200_hub_graph_policy_facts"
down_revision: str | None = "20260726_1200_hub_chunk_work_refs_rename"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (graph_policy_facts,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
