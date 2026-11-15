"""Add runner settings records and nullable launch settings to spawn facts.

Revision ID: 20261115_1000_runner_settings
Revises: 20261111_1000_binding_targets
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261115_1000_runner_settings"
down_revision: str | None = "20261111_1000_binding_targets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("runner_settings", "runner_setting_entries", "runner_setting_changes")
_INDEX = "ix_runner_setting_changes_runner_id_id"


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_TABLES[0]):
        op.create_table(
            _TABLES[0],
            sa.Column("runner_id", sa.String(), primary_key=True),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("updated_at", UtcDateTime(), nullable=False),
        )
    if not sa.inspect(bind).has_table(_TABLES[1]):
        op.create_table(
            _TABLES[1],
            sa.Column("runner_id", sa.String(), nullable=False),
            sa.Column("key", sa.String(), nullable=False),
            sa.Column("value", sa.Text(), nullable=True),
            sa.Column("at_default", sa.Boolean(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.PrimaryKeyConstraint("runner_id", "key"),
        )
    if not sa.inspect(bind).has_table(_TABLES[2]):
        op.create_table(
            _TABLES[2],
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("runner_id", sa.String(), nullable=False),
            sa.Column("recorded_at", UtcDateTime(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("op", sa.String(), nullable=False),
            sa.Column("door", sa.String(), nullable=False),
            sa.Column("actor", sa.String(), nullable=False),
            sa.Column("apply_id", sa.String(), nullable=True),
            sa.Column("diff", sa.Text(), nullable=False),
        )
    if _INDEX not in {index["name"] for index in sa.inspect(bind).get_indexes(_TABLES[2])}:
        op.create_index(_INDEX, _TABLES[2], ["runner_id", "id"])
    columns = {column["name"] for column in sa.inspect(bind).get_columns("lease_spawns")}
    for name, kind in (
        ("host_settings_revision", sa.Integer),
        ("runner_settings_revision", sa.Integer),
        ("transcripts_root", sa.Text),
    ):
        if name not in columns:
            op.add_column("lease_spawns", sa.Column(name, kind(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("lease_spawns")}
    for name in ("transcripts_root", "runner_settings_revision", "host_settings_revision"):
        if name in columns:
            op.drop_column("lease_spawns", name)
    for table in reversed(_TABLES):
        if sa.inspect(bind).has_table(table):
            op.drop_table(table)
