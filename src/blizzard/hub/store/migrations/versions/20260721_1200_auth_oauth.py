"""auth_state / auth_facts — the provider-login seam (issue #92, hub store tree). Created ``checkfirst``;
``auth_state`` is a frozen local literal, not a ``schema.py`` import (``bzh:frozen-revisions``).

Revision ID: 20260721_1200_hub_auth_oauth
Revises: 20260721_1100_hub_auth_identity_spine
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime
from blizzard.hub.store.schema import auth_facts

revision: str = "20260721_1200_hub_auth_oauth"
down_revision: str | None = "20260721_1100_hub_auth_identity_spine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# This revision's own frozen shape — no ``user_id`` column, added by a later revision.
_frozen_metadata = sa.MetaData()
_auth_state = sa.Table(
    "auth_state",
    _frozen_metadata,
    sa.Column("state", sa.String, primary_key=True),
    sa.Column("kind", sa.String, nullable=False),
    sa.Column("provider_name", sa.String, nullable=False),
    sa.Column("return_to", sa.String, nullable=False),
    sa.Column("code_challenge", sa.String, nullable=True),
    sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("expires_at", UtcDateTime, nullable=False),
)

_TABLES = [_auth_state, auth_facts]


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
