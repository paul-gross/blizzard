"""users / identities / sessions — the auth identity spine (hub store tree).
Parents before children so the FKs from ``identities`` and ``sessions`` resolve.

Revision ID: 20260721_1100_hub_auth_identity_spine
Revises: 20260721_1008_hub_graph_node_session_source
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260721_1100_hub_auth_identity_spine"
down_revision: str | None = "20260721_1008_hub_graph_node_session_source"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen (``bzh:frozen-revisions``).
_frozen_metadata = sa.MetaData()
_users = sa.Table(
    "users",
    _frozen_metadata,
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("username", sa.String, nullable=False, unique=True),
    sa.Column("display_name", sa.String, nullable=False),
    sa.Column("email", sa.String, nullable=True),
    sa.Column("role", sa.String, nullable=False),
    sa.Column("created_at", UtcDateTime, nullable=False),
)
sa.Index(
    "uq_users_email",
    _users.c.email,
    unique=True,
    postgresql_where=_users.c.email.isnot(None),
    sqlite_where=_users.c.email.isnot(None),
)
_identities = sa.Table(
    "identities",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("provider_name", sa.String, nullable=False),
    sa.Column("subject", sa.String, nullable=False),
    sa.Column("user_id", sa.String, sa.ForeignKey("users.id"), nullable=False, index=True),
    sa.Column("handle", sa.String, nullable=False),
    sa.Column("created_at", UtcDateTime, nullable=False),
    sa.UniqueConstraint("provider_name", "subject", name="uq_identities_provider_subject"),
)
_sessions = sa.Table(
    "sessions",
    _frozen_metadata,
    sa.Column("id_hash", sa.String, primary_key=True),
    sa.Column("user_id", sa.String, sa.ForeignKey("users.id"), nullable=False, index=True),
    sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("expires_at", UtcDateTime, nullable=False),
    sa.Column("last_seen_at", UtcDateTime, nullable=False),
)

_TABLES = [_users, _identities, _sessions]


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
