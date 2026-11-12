"""Hub-wide settings, entries and audit.

Revision ID: 20261112_0900_hub_settings
Revises: 20261111_1000_chunk_repository_targets
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261112_0900_hub_settings"
down_revision: str | None = "20261111_1000_chunk_repository_targets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("hub_settings"):
        table = op.create_table(
            "hub_settings",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.CheckConstraint("id = 1", name="ck_hub_settings_singleton"),
        )
        op.bulk_insert(table, [{"id": 1, "revision": 0}])
    if not inspector.has_table("hub_setting_entries"):
        op.create_table(
            "hub_setting_entries",
            sa.Column("key", sa.String(), primary_key=True),
            sa.Column("value_json", sa.Text(), nullable=True),
            sa.Column("at_default", sa.Boolean(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
        )
    if not inspector.has_table("hub_setting_changes"):
        op.create_table(
            "hub_setting_changes",
            sa.Column("revision", sa.Integer(), primary_key=True),
            sa.Column("diff_json", sa.Text(), nullable=False),
            sa.Column("op", sa.String(), nullable=False),
            sa.Column("door", sa.String(), nullable=False),
            sa.Column("actor", sa.String(), nullable=False),
            sa.Column("apply_id", sa.String(), nullable=True),
            sa.Column("recorded_at", UtcDateTime(), nullable=False),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table in ("hub_setting_changes", "hub_setting_entries", "hub_settings"):
        if inspector.has_table(table):
            op.drop_table(table)
