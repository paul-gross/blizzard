"""walking-skeleton fact tables (runner store tree) — leases (with pid + start time),
chunk->env bindings, and the outbound buffer. Facts only (``bzh:facts-not-status``).

Revision ID: 20260713_1218_runner_walking_skeleton
Revises: 20260713_1112_runner_initial
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime


revision: str = "20260713_1218_runner_walking_skeleton"
down_revision: str | None = "20260713_1112_runner_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_frozen_metadata = sa.MetaData()
_leases = sa.Table(
    "leases",
    _frozen_metadata,
    sa.Column("lease_id", sa.String(), primary_key=True),
    sa.Column("chunk_id", sa.String(), nullable=False),
    sa.Column("epoch", sa.Integer(), nullable=False),
    sa.Column("runner_id", sa.String(), nullable=False),
    sa.Column("pid", sa.Integer(), nullable=True),
    sa.Column("process_start_time", sa.String(), nullable=True),
    sa.Column("session_id", sa.String(), nullable=True),
    sa.Column("created_at", sa.DateTime(), nullable=False),
)
_env_bindings = sa.Table(
    "env_bindings",
    _frozen_metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String(), nullable=False),
    sa.Column("environment_id", sa.String(), nullable=False),
    sa.Column("workdir", sa.String(), nullable=False),
    sa.Column("bound_at", UtcDateTime(), nullable=False),
)
_outbound_buffer = sa.Table(
    "outbound_buffer",
    _frozen_metadata,
    sa.Column("seq", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("kind", sa.String(), nullable=False),
    sa.Column("chunk_id", sa.String(), nullable=True),
    sa.Column("lease_id", sa.String(), nullable=True),
    sa.Column("payload", sa.Text(), nullable=False),
    sa.Column("created_at", UtcDateTime(), nullable=False),
    sa.Column("acked_at", UtcDateTime(), nullable=True),
)
_TABLES = [_leases, _env_bindings, _outbound_buffer]


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=False)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=False)
