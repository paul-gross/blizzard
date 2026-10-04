"""work_sources, work_source_lifecycle_facts, config_changes — configured work sources and the change log.

Revision ID: 20261004_1100_work_source_records
Revises: 20261004_1000_hub_secrets
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import config_changes, work_source_lifecycle_facts, work_sources

revision: str = "20261004_1100_work_source_records"
down_revision: str | None = "20261004_1000_hub_secrets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (work_sources, work_source_lifecycle_facts, config_changes)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
