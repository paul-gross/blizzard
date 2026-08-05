"""auth_state / auth_facts — the provider-login seam (issue #92, hub store tree). Neither table carries
a foreign key, so either order is safe; created ``checkfirst``, in ``schema.py``'s own order.

Revision ID: 20260721_1200_hub_auth_oauth
Revises: 20260721_1100_hub_auth_identity_spine
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import auth_facts, auth_state

revision: str = "20260721_1200_hub_auth_oauth"
down_revision: str | None = "20260721_1100_hub_auth_identity_spine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = [auth_state, auth_facts]


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
