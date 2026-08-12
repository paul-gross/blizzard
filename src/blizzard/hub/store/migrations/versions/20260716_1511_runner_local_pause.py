"""runner-reported local pause facts (hub store tree) — the runner's own brake (issue #43), a separate
table from ``runner_pause_facts``, the fleet's own brake, because they are separate concepts.

Revision ID: 20260716_1511_hub_runner_local_pause
Revises: 20260715_1817_hub_chunk_promoted
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260716_1511_hub_runner_local_pause"
down_revision: str | None = "20260715_1817_hub_chunk_promoted"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# This revision's own frozen shape — no ``reason`` column, added by a later revision
# (``bzh:frozen-revisions``).
_frozen_metadata = sa.MetaData()
_runner_local_pause_facts = sa.Table(
    "runner_local_pause_facts",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("runner_id", sa.String, nullable=False),
    sa.Column("paused", sa.Boolean, nullable=False),
    sa.Column("set_at", UtcDateTime, nullable=False),
    sa.Column("set_by", sa.String, nullable=False),
)

_TABLES = (_runner_local_pause_facts,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
