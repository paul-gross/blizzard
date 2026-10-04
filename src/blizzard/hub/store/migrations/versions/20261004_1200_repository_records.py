"""repositories, repository_lifecycle_facts — configured repositories.

Revision ID: 20261004_1200_repository_records
Revises: 20261004_1100_work_source_records
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import repositories, repository_lifecycle_facts

revision: str = "20261004_1200_repository_records"
down_revision: str | None = "20261004_1100_work_source_records"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (repositories, repository_lifecycle_facts)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
