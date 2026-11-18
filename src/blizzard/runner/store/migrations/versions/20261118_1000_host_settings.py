"""Add the host settings record.

Revision ID: 20261118_1000_host_settings
Revises: 20261115_1000_runner_settings
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261118_1000_host_settings"
down_revision: str | None = "20261115_1000_runner_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("host_settings", "host_setting_entries", "host_setting_changes")


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_TABLES[0]):
        op.create_table(
            _TABLES[0],
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("updated_at", UtcDateTime(), nullable=False),
            sa.CheckConstraint("id = 1", name="ck_host_settings_singleton"),
        )
    if not sa.inspect(bind).has_table(_TABLES[1]):
        op.create_table(
            _TABLES[1],
            sa.Column("key", sa.String(), primary_key=True),
            sa.Column("value", sa.Text(), nullable=True),
            sa.Column("at_default", sa.Boolean(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
        )
    if not sa.inspect(bind).has_table(_TABLES[2]):
        op.create_table(
            _TABLES[2],
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("recorded_at", UtcDateTime(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("op", sa.String(), nullable=False),
            sa.Column("door", sa.String(), nullable=False),
            sa.Column("actor", sa.String(), nullable=False),
            sa.Column("apply_id", sa.String(), nullable=True),
            sa.Column("diff", sa.Text(), nullable=False),
            sa.Column("record_kind", sa.String(), nullable=False),
            sa.Column("record_key", sa.String(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        if sa.inspect(bind).has_table(table):
            op.drop_table(table)
