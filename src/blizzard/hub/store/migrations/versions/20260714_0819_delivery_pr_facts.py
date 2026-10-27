"""open-PR delivery facts — pr.opened / pr.closed (hub store tree)

Revision ID: 20260714_0819_hub_delivery_pr_facts
Revises: 20260713_1947_hub_runner_registry
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260714_0819_hub_delivery_pr_facts"
down_revision: str | None = "20260713_1947_hub_runner_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen (``bzh:frozen-revisions``): no ``(chunk_id, repo)`` uniqueness; ``chunks`` is an FK stub.
_frozen_metadata = sa.MetaData()
sa.Table(
    "chunks",
    _frozen_metadata,
    sa.Column("chunk_id", sa.String, primary_key=True),
)
_delivery_pr_opened = sa.Table(
    "delivery_pr_opened",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("repo", sa.String, nullable=False),
    sa.Column("pr_number", sa.Integer, nullable=False),
    sa.Column("pr_url", sa.String, nullable=False),
    sa.Column("commit_hash", sa.String, nullable=False),
    sa.Column("opened_at", sa.DateTime, nullable=False),
)
_delivery_pr_closed = sa.Table(
    "delivery_pr_closed",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("repo", sa.String, nullable=False),
    sa.Column("pr_number", sa.Integer, nullable=False),
    sa.Column("merged", sa.Boolean, nullable=False),
    sa.Column("landed_commit", sa.String, nullable=True),
    sa.Column("closed_at", sa.DateTime, nullable=False),
)

_TABLES = [_delivery_pr_opened, _delivery_pr_closed]


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
