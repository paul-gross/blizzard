"""execution observations — ``execution_observations``, one row per sample of a live lease's current execution.
Additive: no existing row is reshaped. A rerun skips the table; downgrade drops it.

Revision ID: 20261114_1000_execution_observations
Revises: 20261111_1000_binding_targets
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261114_1000_execution_observations"
down_revision: str | None = "20261111_1000_binding_targets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "execution_observations"
_INDEX = "ix_execution_observations_lease_id_id"


def upgrade() -> None:
    if _TABLE in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("lease_id", sa.String(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("observed_at", UtcDateTime(), nullable=False),
        sa.Column("liveness", sa.String(), nullable=False),
        sa.Column("activity", sa.String(), nullable=False),
        sa.Column("evidence_at", UtcDateTime(), nullable=True),
        sa.Column("progress_at", UtcDateTime(), nullable=True),
        sa.Column("diagnostic", sa.Text(), nullable=False),
        sa.Column("warned_anchor_at", UtcDateTime(), nullable=True),
        sa.Column("runner_id", sa.String(), nullable=False),
    )
    op.create_index(_INDEX, _TABLE, ["lease_id", "id"])


def downgrade() -> None:
    if _TABLE in sa.inspect(op.get_bind()).get_table_names():
        op.drop_index(_INDEX, table_name=_TABLE)
        op.drop_table(_TABLE)
