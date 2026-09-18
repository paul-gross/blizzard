"""Add durable process ownership and identity facts for a two-phase spawn.

Revision ID: 20260916_1000_two_phase_spawn_ownership
Revises: 20260914_1000_session_harness_provenance
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision = "20260916_1000_two_phase_spawn_ownership"
down_revision = "20260914_1000_session_harness_provenance"
branch_labels = None
depends_on = None

_LEASES = "leases"
_SPAWNS = "lease_spawns"

# All new columns stay nullable, so a plain `add_column` suffices (`bzh:sql-portable`).
_LEASE_COLUMNS = (("pgid", sa.Integer()),)
_SPAWN_COLUMNS = (
    ("pid", sa.Integer()),
    ("process_start_time", sa.String()),
    ("pgid", sa.Integer()),
    ("session_id", sa.String()),
    ("identified_at", UtcDateTime()),
    ("identity_failed_at", UtcDateTime()),
)


def _has_column(bind: sa.Connection, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()

    for name, sa_type in _LEASE_COLUMNS:
        if not _has_column(bind, _LEASES, name):
            op.add_column(_LEASES, sa.Column(name, sa_type, nullable=True))

    for name, sa_type in _SPAWN_COLUMNS:
        if not _has_column(bind, _SPAWNS, name):
            op.add_column(_SPAWNS, sa.Column(name, sa_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()

    for name, _sa_type in reversed(_SPAWN_COLUMNS):
        if _has_column(bind, _SPAWNS, name):
            with op.batch_alter_table(_SPAWNS) as batch:
                batch.drop_column(name)

    for name, _sa_type in reversed(_LEASE_COLUMNS):
        if _has_column(bind, _LEASES, name):
            with op.batch_alter_table(_LEASES) as batch:
                batch.drop_column(name)
