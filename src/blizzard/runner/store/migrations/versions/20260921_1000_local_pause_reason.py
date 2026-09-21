"""Reason onto local_pause_facts.

One guarded, nullable column — un-backfilled, so NULL declares unknown, never a value
(blizzard#594). The reason previously reached only the hub's own outbound report; this
lets the runner's own status wire read it back.
Revision ID: 20260921_1000_local_pause_reason
Revises: 20260920_0300_usage_harness_provenance
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260921_1000_local_pause_reason"
down_revision = "20260920_0300_usage_harness_provenance"
branch_labels = None
depends_on = None

_LOCAL_PAUSE_FACTS = "local_pause_facts"
_COLUMN = "reason"


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if _COLUMN not in _columns(bind, _LOCAL_PAUSE_FACTS):
        op.add_column(_LOCAL_PAUSE_FACTS, sa.Column(_COLUMN, sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _COLUMN in _columns(bind, _LOCAL_PAUSE_FACTS):
        with op.batch_alter_table(_LOCAL_PAUSE_FACTS) as batch:
            batch.drop_column(_COLUMN)
