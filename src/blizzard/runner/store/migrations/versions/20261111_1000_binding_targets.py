"""binding targets — the new ``binding_targets`` table, one row per repository target an environment binding was
prepared at, written in the binding's own transaction.

A binding recorded under the prior release has no rows: it was prepared at its workspace's own branch, and its claim
names no targets. A rerun skips the table when present. ``downgrade()`` drops the table.

Revision ID: 20261111_1000_binding_targets
Revises: 20261029_1000_takeover_entries
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261111_1000_binding_targets"
down_revision: str | None = "20261029_1000_takeover_entries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "binding_targets"
_INDEX = "ix_binding_targets_binding_id"


def upgrade() -> None:
    if _TABLE in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("binding_id", sa.Integer(), sa.ForeignKey("env_bindings.id"), nullable=False),
        sa.Column("repository_id", sa.String(), nullable=False),
        sa.Column("repository_slug", sa.String(), nullable=False),
        sa.Column("forge_host", sa.String(), nullable=False),
        sa.Column("owner", sa.String(), nullable=False),
        sa.Column("repo", sa.String(), nullable=False),
        sa.Column("base_branch", sa.String(), nullable=False),
        sa.Column("inherited", sa.Boolean(), nullable=False),
        sa.Column("runner_id", sa.String(), nullable=False),
    )
    op.create_index(_INDEX, _TABLE, ["binding_id"])


def downgrade() -> None:
    if _TABLE in sa.inspect(op.get_bind()).get_table_names():
        op.drop_index(_INDEX, table_name=_TABLE)
        op.drop_table(_TABLE)
