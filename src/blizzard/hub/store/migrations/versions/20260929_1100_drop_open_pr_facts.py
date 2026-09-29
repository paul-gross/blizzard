"""Drop the retired open-PR fact tables, ``delivery_pr_opened`` and ``delivery_pr_closed``.

Destructive: ``downgrade()`` recreates both empty, never the rows.

Revision ID: 20260929_1100_drop_open_pr_facts
Revises: 20260929_1000_decision_imposed_by_runner
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260929_1100_drop_open_pr_facts"
down_revision: str | None = "20260929_1000_decision_imposed_by_runner"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OPENED = "delivery_pr_opened"
_CLOSED = "delivery_pr_closed"
_OPENED_UNIQUE = "uq_delivery_pr_opened_chunk_repo"
_CLOSED_INDEX = "ix_delivery_pr_closed_chunk_id"


def upgrade() -> None:
    op.drop_index(_CLOSED_INDEX, table_name=_CLOSED)
    op.drop_table(_CLOSED)
    op.drop_table(_OPENED)


def downgrade() -> None:
    op.create_table(
        _OPENED,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
        sa.Column("repo", sa.String, nullable=False),
        sa.Column("pr_number", sa.Integer, nullable=False),
        sa.Column("pr_url", sa.String, nullable=False),
        sa.Column("commit_hash", sa.String, nullable=False),
        sa.Column("opened_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("chunk_id", "repo", name=_OPENED_UNIQUE),
    )
    op.create_table(
        _CLOSED,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
        sa.Column("repo", sa.String, nullable=False),
        sa.Column("pr_number", sa.Integer, nullable=False),
        sa.Column("merged", sa.Boolean, nullable=False),
        sa.Column("landed_commit", sa.String, nullable=True),
        sa.Column("closed_at", sa.DateTime, nullable=False),
    )
    op.create_index(_CLOSED_INDEX, _CLOSED, ["chunk_id"])
