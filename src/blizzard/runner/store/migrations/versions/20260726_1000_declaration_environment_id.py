"""git-commit declarations keyed by environment, not by a worker-supplied forge

Two coupled changes to ``git_commit_declarations``, both about what identifies a
declaration:

- **drop ``forge``** — the origin a declaration is verified against now comes from the
  environment's repo manifest (the workspace provider owns the layout), not from the
  worker. The column's value was derived from ``git remote get-url origin`` in the
  worker's process cwd; workers are spawned at the workspace root, so every declaration
  named the enclosing workspace repo rather than the repo it described, failed the
  verify comparison, and was dropped silently.
- **add ``environment_id``** — a chunk may hold several environments, each with its own
  worktree of the same repo, so ``(lease_id, repo)`` named a branch ambiguously and a
  second env's declaration read as a *correction* of the first rather than a second
  fact. The read is now newest-per-``(lease_id, environment_id, repo)``.

Existing rows carry no environment and a forge that never verified, so there is nothing
to preserve: the table is dropped and recreated rather than back-filled. That is safe
precisely because a declaration is ephemeral — it is read back within the lease that
wrote it, and any lease old enough to predate this revision has long since closed. A
back-fill would have to invent an ``environment_id`` for rows whose env is unknowable,
which is worse than starting clean.

Revision ID: 20260726_1000_runner_declaration_environment_id
Revises: 20260725_1200_runner_check_results
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime
from blizzard.runner.store.schema import git_commit_declarations

revision: str = "20260726_1000_runner_declaration_environment_id"
down_revision: str | None = "20260725_1200_runner_check_results"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    # Drop-and-recreate rather than ALTER: sqlite cannot drop a column in place, and the
    # rows hold nothing worth migrating (see the module docstring).
    git_commit_declarations.drop(bind, checkfirst=True)
    git_commit_declarations.create(bind, checkfirst=True)


def downgrade() -> None:
    # Recreate the pre-revision shape explicitly — `schema.py` now describes the new one,
    # so the old columns are spelled out here rather than imported.
    bind = op.get_bind()
    git_commit_declarations.drop(bind, checkfirst=True)
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
