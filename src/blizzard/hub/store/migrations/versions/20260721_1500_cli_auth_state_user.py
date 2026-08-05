"""auth_state.user_id — the CLI code-exchange's owning user, nullable and with no
`ForeignKey` (SQLite cannot drop an FK column) (hub store tree, issue #96)

Revision ID: 20260721_1500_hub_cli_auth_state_user
Revises: 20260721_1400_hub_runner_redirect_uris
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_1500_hub_cli_auth_state_user"
down_revision: str | None = "20260721_1400_hub_runner_redirect_uris"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "auth_state"
_COLUMN = "user_id"


def _existing_columns(bind: sa.Connection) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if _COLUMN not in _existing_columns(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _COLUMN in _existing_columns(bind):
        op.drop_column(_TABLE, _COLUMN)
