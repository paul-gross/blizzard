"""hub-minted runner bearer tokens: the sha256 token_hash column plus a lookup index on
it, since a presented token resolves to *its* runner (hub store tree, issue #86a)

Revision ID: 20260718_1130_hub_runner_token
Revises: 20260718_0030_hub_node_poll
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_1130_hub_runner_token"
down_revision: str | None = "20260718_0030_hub_node_poll"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "runner_registrations"
_COLUMN = "token_hash"
_INDEX = "ix_runner_registrations_token_hash"


def _has_column(bind: sa.Connection) -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def _has_index(bind: sa.Connection) -> bool:
    return _INDEX in {i["name"] for i in sa.inspect(bind).get_indexes(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Text(), nullable=True))
    if not _has_index(bind):
        op.create_index(_INDEX, _TABLE, [_COLUMN])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_index(bind):
        op.drop_index(_INDEX, table_name=_TABLE)
    if _has_column(bind):
        op.drop_column(_TABLE, _COLUMN)
