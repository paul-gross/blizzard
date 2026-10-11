"""git-commit declarations keyed by environment, not by a worker-supplied forge — drops
``forge``, adds ``environment_id``. Existing rows are discarded, never back-filled.

Revision ID: 20260726_1000_runner_declaration_environment_id
Revises: 20260725_1200_runner_check_results
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260726_1000_runner_declaration_environment_id"
down_revision: str | None = "20260725_1200_runner_check_results"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_frozen_metadata = sa.MetaData()
_git_commit_declarations = sa.Table(
    "git_commit_declarations",
    _frozen_metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("lease_id", sa.String(), nullable=False),
    sa.Column("chunk_id", sa.String(), nullable=False),
    sa.Column("node_id", sa.String(), nullable=False),
    sa.Column("epoch", sa.Integer(), nullable=False),
    sa.Column("environment_id", sa.String(), nullable=False),
    sa.Column("repo", sa.String(), nullable=False),
    sa.Column("branch", sa.String(), nullable=False),
    sa.Column("commit", sa.String(), nullable=False),
    sa.Column("declared_at", UtcDateTime(), nullable=False),
)


def upgrade() -> None:
    bind = op.get_bind()
    # Drop-and-recreate rather than ALTER: sqlite cannot drop a column in place, and the
    # rows hold nothing worth migrating (see the module docstring).
    _git_commit_declarations.drop(bind, checkfirst=True)
    _git_commit_declarations.create(bind, checkfirst=True)


def downgrade() -> None:
    # Recreate the pre-revision shape explicitly, as the frozen literal above describes the new one,
    # so the old columns are spelled out here.
    bind = op.get_bind()
    _git_commit_declarations.drop(bind, checkfirst=True)
    sa.Table(
        "git_commit_declarations",
        sa.MetaData(),
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
    ).create(bind, checkfirst=True)
