"""superuser_bootstrap — the ``auth.superuser`` bootstrap lifecycle (hub store tree)

A singleton row carrying a foreign key onto ``users``, so this revision follows the identity spine.
Revision ID: 20260721_1300_hub_auth_superuser_bootstrap
Revises: 20260721_1200_hub_auth_oauth
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260721_1300_hub_auth_superuser_bootstrap"
down_revision: str | None = "20260721_1200_hub_auth_oauth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen (``bzh:frozen-revisions``); ``users`` is an FK stub.
_frozen_metadata = sa.MetaData()
sa.Table(
    "users",
    _frozen_metadata,
    sa.Column("id", sa.String, primary_key=True),
)
_superuser_bootstrap = sa.Table(
    "superuser_bootstrap",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("email", sa.String, nullable=False),
    sa.Column("claimed_user_id", sa.String, sa.ForeignKey("users.id"), nullable=True),
    sa.Column("updated_at", UtcDateTime, nullable=False),
)


def upgrade() -> None:
    _superuser_bootstrap.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    _superuser_bootstrap.drop(op.get_bind(), checkfirst=True)
