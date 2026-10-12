"""route capability token stash — route_tokens (runner store tree). One
upserted row per chunk; the runner keeps no rotation history, only its current token.

Revision ID: 20260718_1200_runner_route_tokens
Revises: 20260717_2200_runner_usage_facts
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime


revision: str = "20260718_1200_runner_route_tokens"
down_revision: str | None = "20260717_2200_runner_usage_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_frozen_metadata = sa.MetaData()
_route_tokens = sa.Table(
    "route_tokens",
    _frozen_metadata,
    sa.Column("chunk_id", sa.String(), primary_key=True),
    sa.Column("token", sa.Text(), nullable=False),
    sa.Column("acquired_at", UtcDateTime(), nullable=False),
)

_TABLES = (_route_tokens,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
