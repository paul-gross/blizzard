"""graph lifecycle facts — a reversible retire/re-enable brake over one graph_id

Append-only, newest-fact-wins, created ``checkfirst``; the ``graphs`` table itself stays immutable.
Revision ID: 20260719_0900_hub_graph_lifecycle_facts
Revises: 20260718_1300_hub_runner_env_capacity
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import graph_lifecycle_facts

revision: str = "20260719_0900_hub_graph_lifecycle_facts"
down_revision: str | None = "20260718_1300_hub_runner_env_capacity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (graph_lifecycle_facts,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
