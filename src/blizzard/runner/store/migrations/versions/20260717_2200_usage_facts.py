"""usage facts — one append-only row per harness invocation's cost/token telemetry, never
a stored aggregate (runner store tree, issue #58, ``bzh:facts-not-status``)

Revision ID: 20260717_2200_runner_usage_facts
Revises: 20260717_0446_runner_pause_parks
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_2200_runner_usage_facts"
down_revision: str | None = "20260717_2300_runner_requeues"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_frozen_metadata = sa.MetaData()
_usage_facts = sa.Table(
    "usage_facts",
    _frozen_metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("lease_id", sa.String(), nullable=False),
    sa.Column("chunk_id", sa.String(), nullable=False),
    sa.Column("node_id", sa.String(), nullable=False),
    sa.Column("epoch", sa.Integer(), nullable=False),
    sa.Column("generation", sa.Integer(), nullable=False),
    sa.Column("kind", sa.String(), nullable=False),
    sa.Column("model", sa.String(), nullable=False),
    sa.Column("input_tokens", sa.Integer(), nullable=False),
    sa.Column("output_tokens", sa.Integer(), nullable=False),
    sa.Column("cache_read_tokens", sa.Integer(), nullable=False),
    sa.Column("cache_create_tokens", sa.Integer(), nullable=False),
    sa.Column("cost_usd", sa.Float(), nullable=True),
    sa.Column("recorded_at", sa.DateTime(), nullable=False),
)
_TABLES = (_usage_facts,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
