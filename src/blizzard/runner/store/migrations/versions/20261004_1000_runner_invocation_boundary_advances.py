"""The append-only advances of a standing invocation boundary's start.

One guarded table: each advance is its own fact, the marker row is never rewritten.
Revision ID: 20261004_1000_runner_invocation_boundary_advances
Revises: 20261004_1000_binding_release_epoch_floor
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261004_1000_runner_invocation_boundary_advances"
down_revision: str | None = "20261004_1000_binding_release_epoch_floor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "invocation_boundary_advances"
_INDEX = "ix_invocation_boundary_advances_boundary"


def upgrade() -> None:
    bind = op.get_bind()
    if _TABLE in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("lease_id", sa.String(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("superseded_invocation", sa.String(), nullable=False),
        sa.Column("start_position", sa.String(), nullable=True),
        sa.Column("start_unreadable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("advanced_at", UtcDateTime(), nullable=False),
    )
    op.create_index(_INDEX, _TABLE, ["lease_id", "generation", "kind"])


def downgrade() -> None:
    bind = op.get_bind()
    if _TABLE in sa.inspect(bind).get_table_names():
        op.drop_table(_TABLE)
