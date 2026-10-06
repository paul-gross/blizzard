"""hub control mirror — the last ``paused`` value read back from the hub registry, kept
locally so it survives the hub being unreachable (runner store tree)

Revision ID: 20260713_1946_runner_hub_control
Revises: 20260713_1801_runner_asks_and_parks
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260713_1946_runner_hub_control"
down_revision: str | None = "20260713_1801_runner_asks_and_parks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen literal (`bzh:frozen-revisions`): the table as this revision creates it, keyed by runner id.
_hub_control = sa.Table(
    "hub_control",
    sa.MetaData(),
    sa.Column("runner_id", sa.String(), primary_key=True),
    sa.Column("paused", sa.Boolean(), nullable=False),
    sa.Column("updated_at", UtcDateTime(), nullable=False),
)

_TABLES = (_hub_control,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
