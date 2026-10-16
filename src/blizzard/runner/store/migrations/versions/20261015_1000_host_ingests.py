"""host ingests — a new host-scoped ``host_ingests`` table, one row per runner the host took in from another store: the
runner, the resolved source, the source's last liveness stamp (NULL if it never ticked), and when. Created empty; no
other table changes, and a rerun skips it. ``downgrade()`` drops it: its rows only let an interrupted ingest resume.
Revision ID: 20261015_1000_host_ingests
Revises: 20261013_1000_runner_scope_tighten
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261015_1000_host_ingests"
down_revision: str | None = "20261013_1000_runner_scope_tighten"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "host_ingests"


def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table(_TABLE):
        op.create_table(
            _TABLE,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("runner_id", sa.String(), nullable=False),
            sa.Column("source", sa.String(), nullable=False),
            sa.Column("source_alive_at", UtcDateTime(), nullable=True),
            sa.Column("ingested_at", UtcDateTime(), nullable=False),
        )


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(_TABLE):
        op.drop_table(_TABLE)
