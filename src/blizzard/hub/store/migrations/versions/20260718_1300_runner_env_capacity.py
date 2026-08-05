"""runner environment-pool capacity: ``env_capacity`` on the registry (issue #69). Nullable — a client
predating the field reports none rather than a guessed total. A rotating column, added idempotently.

Revision ID: 20260718_1300_hub_runner_env_capacity
Revises: 20260718_1225_hub_chunk_migrations
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_1300_hub_runner_env_capacity"
down_revision: str | None = "20260718_1225_hub_chunk_migrations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "runner_registrations"
_COLUMN = "env_capacity"


def _has_column(bind: sa.Connection) -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind):
        op.drop_column(_TABLE, _COLUMN)
