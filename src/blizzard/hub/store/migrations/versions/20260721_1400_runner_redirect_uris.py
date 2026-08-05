"""runner federation registration: ``public_url`` + JSON-encoded ``redirect_uris`` on the
registry, both nullable and rewritten in place on re-registration (hub store tree, #95)

Revision ID: 20260721_1400_hub_runner_redirect_uris
Revises: 20260721_1300_hub_auth_superuser_bootstrap
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_1400_hub_runner_redirect_uris"
down_revision: str | None = "20260721_1300_hub_auth_superuser_bootstrap"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "runner_registrations"
_COLUMNS = ("public_url", "redirect_uris")


def _existing_columns(bind: sa.Connection) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    existing = _existing_columns(bind)
    for column in _COLUMNS:
        if column not in existing:
            op.add_column(_TABLE, sa.Column(column, sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing = _existing_columns(bind)
    for column in _COLUMNS:
        if column in existing:
            op.drop_column(_TABLE, column)
