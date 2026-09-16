"""scopes, scope_lifecycle_facts, routines — the routine-and-scope hub entities
(blizzard#389); `routines` is frozen (`bzh:frozen-revisions`) — reshaped by 20260916_1000_hub_authored_harnesses.

Revision ID: 20260828_1000_scopes_and_routines
Revises: 20260826_0930_close_intents_backfill
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime
from blizzard.hub.store.schema import scope_lifecycle_facts, scopes

revision: str = "20260828_1000_scopes_and_routines"
down_revision: str | None = "20260826_0930_close_intents_backfill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen (`bzh:frozen-revisions`) — no `default_harnesses`; `scopes` is an FK-resolution stub.
_frozen_metadata = sa.MetaData()
sa.Table(
    "scopes",
    _frozen_metadata,
    sa.Column("slug", sa.String, primary_key=True),
)
_routines = sa.Table(
    "routines",
    _frozen_metadata,
    sa.Column("routine_id", sa.String, primary_key=True),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("graph_name", sa.String, nullable=False),
    sa.Column("default_scope_slug", sa.String, sa.ForeignKey("scopes.slug"), nullable=False),
    sa.Column("default_model", sa.Text, nullable=True),
    sa.Column("default_effort", sa.String, nullable=True),
    sa.Column("created_at", UtcDateTime, nullable=False),
    sa.UniqueConstraint("name", name="uq_routines_name"),
)

_TABLES = (scopes, scope_lifecycle_facts, _routines)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
