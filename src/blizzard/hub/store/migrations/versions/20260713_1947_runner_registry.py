"""fleet registry — runner registrations and pause/resume facts (hub store tree)

``runner_pause_facts`` is append-only, newest-fact-wins; parents before children.
Revision ID: 20260713_1947_hub_runner_registry
Revises: 20260713_1946_hub_queue_shaping
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260713_1947_hub_runner_registry"
down_revision: str | None = "20260713_1946_hub_queue_shaping"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen (``bzh:frozen-revisions``): no ``token_hash``/``env_capacity``/``public_url``/``redirect_uris`` columns.
_frozen_metadata = sa.MetaData()
_runner_registrations = sa.Table(
    "runner_registrations",
    _frozen_metadata,
    sa.Column("runner_id", sa.String, primary_key=True),
    sa.Column("workspace_id", sa.String, nullable=False),
    sa.Column("registered_at", UtcDateTime, nullable=False),
    sa.Column("last_seen_at", UtcDateTime, nullable=False),
)

# Frozen (``bzh:frozen-revisions``).
_runner_pause_facts = sa.Table(
    "runner_pause_facts",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("runner_id", sa.String, sa.ForeignKey("runner_registrations.runner_id"), nullable=False),
    sa.Column("paused", sa.Boolean, nullable=False),
    sa.Column("set_at", UtcDateTime, nullable=False),
    sa.Column("set_by", sa.String, nullable=False),
)

_TABLES = [_runner_registrations, _runner_pause_facts]


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
