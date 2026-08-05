"""users / identities / sessions — the auth identity spine (issue #91, hub store tree).
Parents before children so the FKs from ``identities`` and ``sessions`` resolve.

Revision ID: 20260721_1100_hub_auth_identity_spine
Revises: 20260721_1008_hub_graph_node_session_source
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import identities, sessions, users

revision: str = "20260721_1100_hub_auth_identity_spine"
down_revision: str | None = "20260721_1008_hub_graph_node_session_source"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = [users, identities, sessions]


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
