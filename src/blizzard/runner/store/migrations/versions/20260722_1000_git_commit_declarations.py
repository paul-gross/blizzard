"""worker git-commit declaration channel — one append-only row per declare call,
latest-wins per ``(lease_id, repo)`` (runner store tree, issue #143)

Revision ID: 20260722_1000_runner_git_commit_declarations
Revises: 20260721_1500_runner_jwt_jti_seen
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260722_1000_runner_git_commit_declarations"
down_revision: str | None = "20260721_1500_runner_jwt_jti_seen"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# This revision's own frozen shape — carries ``forge``, not ``environment_id``: a later
# revision drops and recreates this table to swap the two (``bzh:frozen-revisions``).
_frozen_metadata = sa.MetaData()
_git_commit_declarations = sa.Table(
    "git_commit_declarations",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("lease_id", sa.String, nullable=False),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("node_id", sa.String, nullable=False),
    sa.Column("epoch", sa.Integer, nullable=False),
    sa.Column("forge", sa.String, nullable=False),
    sa.Column("repo", sa.String, nullable=False),
    sa.Column("branch", sa.String, nullable=False),
    sa.Column("commit", sa.String, nullable=False),
    sa.Column("declared_at", UtcDateTime, nullable=False),
)

_TABLES = (_git_commit_declarations,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
