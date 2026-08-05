"""open-PR delivery facts — pr.opened / pr.closed; ``delivery_pr_opened`` is a frozen
local literal rather than a ``schema.py`` import (hub store tree, ``canon:no-retro``)

Revision ID: 20260714_0819_hub_delivery_pr_facts
Revises: 20260713_1947_hub_runner_registry
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.hub.store.schema import delivery_pr_closed

revision: str = "20260714_0819_hub_delivery_pr_facts"
down_revision: str | None = "20260713_1947_hub_runner_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# This revision's own frozen shape — no (chunk_id, repo) uniqueness — reshaped by 0014.
# The `chunks` entry below is an FK-resolution stub: never created, never dropped.
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

_TABLES = [_delivery_pr_opened, delivery_pr_closed]


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
