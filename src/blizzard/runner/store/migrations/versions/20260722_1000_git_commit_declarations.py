"""worker git-commit declaration channel — one append-only row per declare call,
latest-wins per ``(lease_id, repo)`` (runner store tree, issue #143)

Revision ID: 20260722_1000_runner_git_commit_declarations
Revises: 20260721_1500_runner_jwt_jti_seen
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import git_commit_declarations

revision: str = "20260722_1000_runner_git_commit_declarations"
down_revision: str | None = "20260721_1500_runner_jwt_jti_seen"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (git_commit_declarations,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
