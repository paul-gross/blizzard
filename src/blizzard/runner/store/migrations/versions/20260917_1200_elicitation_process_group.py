"""Add the durable process-group column an elicitation launch owns.

Revision ID: 20260917_1200_elicitation_process_group
Revises: 20260916_1000_two_phase_spawn_ownership
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260917_1200_elicitation_process_group"
down_revision = "20260916_1000_two_phase_spawn_ownership"
branch_labels = None
depends_on = None

_ELICITATIONS = "in_flight_elicitations"
_COLUMN = "pgid"


def _has_column(bind: sa.Connection, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, _ELICITATIONS, _COLUMN):
        op.add_column(_ELICITATIONS, sa.Column(_COLUMN, sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, _ELICITATIONS, _COLUMN):
        with op.batch_alter_table(_ELICITATIONS) as batch:
            batch.drop_column(_COLUMN)
