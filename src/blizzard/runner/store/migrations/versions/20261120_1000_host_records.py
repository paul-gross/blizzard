"""Add the host's workspace and subscription records and their append-only lifecycle facts.

Revision ID: 20261120_1000_host_records
Revises: 20261119_1000_hosted_runners
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261120_1000_host_records"
down_revision: str | None = "20261119_1000_hosted_runners"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORKSPACES = "host_workspaces"
_WORKSPACE_FACTS = "host_workspace_facts"
_WORKSPACE_FACTS_INDEX = "ix_host_workspace_facts_workspace_id_id"
_SUBSCRIPTIONS = "host_subscriptions"
_SUBSCRIPTION_FACTS = "host_subscription_facts"
_SUBSCRIPTION_FACTS_INDEX = "ix_host_subscription_facts_slug_id"


def _facts(table: str, key: str, index: str) -> None:
    op.create_table(
        table,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(key, sa.String(), nullable=False),
        sa.Column("recorded_at", UtcDateTime(), nullable=False),
        sa.Column("op", sa.String(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("door", sa.String(), nullable=False),
    )
    op.create_index(index, table, [key, "id"])


def upgrade() -> None:
    held = set(sa.inspect(op.get_bind()).get_table_names())
    if _WORKSPACES not in held:
        op.create_table(
            _WORKSPACES,
            sa.Column("workspace_id", sa.String(), primary_key=True),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("settings", sa.Text(), nullable=False),
            sa.Column("created_at", UtcDateTime(), nullable=False),
        )
    if _WORKSPACE_FACTS not in held:
        _facts(_WORKSPACE_FACTS, "workspace_id", _WORKSPACE_FACTS_INDEX)
    if _SUBSCRIPTIONS not in held:
        op.create_table(
            _SUBSCRIPTIONS,
            sa.Column("slug", sa.String(), primary_key=True),
            sa.Column("provider", sa.String(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("settings", sa.Text(), nullable=False),
            sa.Column("created_at", UtcDateTime(), nullable=False),
        )
    if _SUBSCRIPTION_FACTS not in held:
        _facts(_SUBSCRIPTION_FACTS, "slug", _SUBSCRIPTION_FACTS_INDEX)


def downgrade() -> None:
    held = set(sa.inspect(op.get_bind()).get_table_names())
    for table, index in (
        (_SUBSCRIPTION_FACTS, _SUBSCRIPTION_FACTS_INDEX),
        (_WORKSPACE_FACTS, _WORKSPACE_FACTS_INDEX),
    ):
        if table in held:
            op.drop_index(index, table_name=table)
            op.drop_table(table)
    for table in (_SUBSCRIPTIONS, _WORKSPACES):
        if table in held:
            op.drop_table(table)
