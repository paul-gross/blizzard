"""lease capability token stash — ``lease_tokens`` (runner store tree): one row per lease,
written at spawn. The plaintext rides the spawn env and is never persisted, only its sha256.

Revision ID: 20260719_0900_runner_lease_tokens
Revises: 20260718_1200_runner_route_tokens
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260719_0900_runner_lease_tokens"
down_revision: str | None = "20260718_1200_runner_route_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_frozen_metadata = sa.MetaData()
_lease_tokens = sa.Table(
    "lease_tokens",
    _frozen_metadata,
    sa.Column("lease_id", sa.String, primary_key=True),
    sa.Column("token_hash", sa.Text, nullable=False),
    sa.Column("minted_at", UtcDateTime, nullable=False),
)

_TABLES = (_lease_tokens,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
