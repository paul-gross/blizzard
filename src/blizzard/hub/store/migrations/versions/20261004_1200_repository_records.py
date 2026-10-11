"""repositories, repository_lifecycle_facts — configured repositories.

Revision ID: 20261004_1200_repository_records
Revises: 20261004_1100_work_source_records
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261004_1200_repository_records"
down_revision: str | None = "20261004_1100_work_source_records"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen (``bzh:frozen-revisions``): ``repositories`` and ``repository_lifecycle_facts`` are local literals of
# the hub store's declared shape. ``secrets`` is an FK-resolution stub: never created, never dropped.
_frozen_metadata = sa.MetaData()
sa.Table(
    "secrets",
    _frozen_metadata,
    sa.Column("name", sa.String, primary_key=True),
)
_repositories = sa.Table(
    "repositories",
    _frozen_metadata,
    sa.Column("name", sa.String, primary_key=True),
    sa.Column("forge_api_url", sa.String, nullable=False),
    sa.Column("owner", sa.String, nullable=False),
    sa.Column("repo", sa.String, nullable=False),
    sa.Column("base_branch", sa.String, nullable=False),
    sa.Column("secret_name", sa.String, sa.ForeignKey("secrets.name"), nullable=False),
    sa.Column("revision", sa.Integer, nullable=False),
    sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("created_by", sa.String, nullable=False),
    sa.UniqueConstraint("forge_api_url", "owner", "repo", name="uq_repositories_coordinate"),
)
_repository_lifecycle_facts = sa.Table(
    "repository_lifecycle_facts",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("name", sa.String, sa.ForeignKey("repositories.name"), nullable=False),
    sa.Column("retired", sa.Boolean, nullable=False),
    sa.Column("set_at", UtcDateTime, nullable=False),
    sa.Column("set_by", sa.String, nullable=False),
)

_TABLES = (_repositories, _repository_lifecycle_facts)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
