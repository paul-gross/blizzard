"""Record runner session owners and invocation provenance.

Revision ID: 20260914_1000_session_harness_provenance
Revises: 20260913_1100_runner_store_indexes
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260914_1000_session_harness_provenance"
down_revision = "20260913_1100_runner_store_indexes"
branch_labels = None
depends_on = None

# Frozen — never imported from the live registry, so a later rename can't change this upgrade.
_HISTORICAL_HARNESS_ID = "claude_code"

_HARNESS_ID = "harness_id"
_SESSION_OWNED_TABLES = ("leases", "asks", "takeovers")
_ALWAYS_OWNED_TABLES = ("session_preamble_facts", "context_samples", "transcript_segments")
_SPAWNS = "lease_spawns"


def _has_column(bind: sa.Connection, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()

    for table in _SESSION_OWNED_TABLES:
        if not _has_column(bind, table, _HARNESS_ID):
            op.add_column(table, sa.Column(_HARNESS_ID, sa.String(), nullable=True))
            # All historical session-bearing observations belong to the only production harness.
            op.execute(
                sa.text(f"UPDATE {table} SET harness_id = :owner WHERE session_id IS NOT NULL").bindparams(
                    owner=_HISTORICAL_HARNESS_ID
                )
            )

    # These tables never have a session-less row.  Batch alteration keeps SQLite and
    # PostgreSQL on the same logical schema without backend-specific DDL.
    for table in _ALWAYS_OWNED_TABLES:
        if not _has_column(bind, table, _HARNESS_ID):
            op.add_column(table, sa.Column(_HARNESS_ID, sa.String(), nullable=True))
            op.execute(sa.text(f"UPDATE {table} SET harness_id = :owner").bindparams(owner=_HISTORICAL_HARNESS_ID))
            with op.batch_alter_table(table) as batch:
                batch.alter_column(_HARNESS_ID, existing_type=sa.String(), nullable=False)

    if not _has_column(bind, _SPAWNS, _HARNESS_ID):
        op.add_column(_SPAWNS, sa.Column(_HARNESS_ID, sa.String(), nullable=True))
        # Version was not observed at this old boundary, so it remains NULL.
        op.execute(sa.text(f"UPDATE {_SPAWNS} SET harness_id = :owner").bindparams(owner=_HISTORICAL_HARNESS_ID))
    if not _has_column(bind, _SPAWNS, "harness_version"):
        op.add_column(_SPAWNS, sa.Column("harness_version", sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()

    if _has_column(bind, _SPAWNS, "harness_version"):
        with op.batch_alter_table(_SPAWNS) as batch:
            batch.drop_column("harness_version")
    if _has_column(bind, _SPAWNS, _HARNESS_ID):
        with op.batch_alter_table(_SPAWNS) as batch:
            batch.drop_column(_HARNESS_ID)

    for table in (*_SESSION_OWNED_TABLES, *_ALWAYS_OWNED_TABLES):
        if _has_column(bind, table, _HARNESS_ID):
            with op.batch_alter_table(table) as batch:
                batch.drop_column(_HARNESS_ID)
