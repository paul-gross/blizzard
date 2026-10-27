"""runner retirement — the lifecycle fact and token-revocation tables (new, ``checkfirst``),
plus ``ix_route_created_runner_id`` for the by-runner holdings read.

Revision ID: 20260928_1100_runner_retirement
Revises: 20260928_1000_queue_positions_chunk_id_index
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260928_1100_runner_retirement"
down_revision: str | None = "20260928_1000_queue_positions_chunk_id_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen (``bzh:frozen-revisions``); ``runner_registrations`` is an FK stub.
_frozen_metadata = sa.MetaData()
sa.Table(
    "runner_registrations",
    _frozen_metadata,
    sa.Column("runner_id", sa.String, primary_key=True),
)
_runner_lifecycle_facts = sa.Table(
    "runner_lifecycle_facts",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("runner_id", sa.String, sa.ForeignKey("runner_registrations.runner_id"), nullable=False),
    sa.Column("retired", sa.Boolean, nullable=False),
    sa.Column("set_at", UtcDateTime, nullable=False),
    sa.Column("set_by", sa.String, nullable=False),
)
_runner_token_revocations = sa.Table(
    "runner_token_revocations",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("runner_id", sa.String, sa.ForeignKey("runner_registrations.runner_id"), nullable=False),
    sa.Column("token_hash", sa.Text, nullable=False, index=True),
    sa.Column("revoked_at", UtcDateTime, nullable=False),
    sa.Column("revoked_by", sa.String, nullable=False),
)

_TABLES = (_runner_lifecycle_facts, _runner_token_revocations)
_INDEX_NAME = "ix_route_created_runner_id"


def _has_index() -> bool:
    return any(ix["name"] == _INDEX_NAME for ix in sa.inspect(op.get_bind()).get_indexes("route_created"))


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)
    if not _has_index():
        op.create_index(_INDEX_NAME, "route_created", ["runner_id"])


def downgrade() -> None:
    if _has_index():
        op.drop_index(_INDEX_NAME, table_name="route_created")
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
