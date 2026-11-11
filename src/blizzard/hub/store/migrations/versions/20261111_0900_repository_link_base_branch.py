"""repository link base branch — a project's link to a repository may name its own integration branch.

Adds nullable `project_repository_links.base_branch`, no backfill: an existing link inherits. Downgrade drops it.
Revision ID: 20261111_0900_repository_link_base_branch
Revises: 20261109_0900_urgency_references
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261111_0900_repository_link_base_branch"
down_revision: str | None = "20261109_0900_urgency_references"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LINKS = "project_repository_links"
_COLUMN = "base_branch"


def _held(bind: sa.Connection) -> bool:
    return _COLUMN in {str(c["name"]) for c in sa.inspect(bind).get_columns(_LINKS)}


def _clear_stale_copy(bind: sa.Connection) -> None:
    """Drop the copy a stopped batch left (SQLite makes it outside the batch's transaction, under Alembic's name)."""
    copy = f"_alembic_tmp_{_LINKS}"[:50]
    if sa.inspect(bind).has_table(copy):
        op.drop_table(copy)


def _reflected(bind: sa.Connection) -> sa.Table:
    """The table as the store holds it, so a copying batch keeps what its own reflection drops."""
    return sa.Table(_LINKS, sa.MetaData(), autoload_with=bind)


def upgrade() -> None:
    if not _held(op.get_bind()):
        op.add_column(_LINKS, sa.Column(_COLUMN, sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if not _held(bind):
        return
    _clear_stale_copy(bind)
    with op.batch_alter_table(_LINKS, recreate="auto", copy_from=_reflected(bind)) as batch:
        batch.drop_column(_COLUMN)
