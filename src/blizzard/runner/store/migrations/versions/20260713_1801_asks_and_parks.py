"""asks and park/resume fact tables (runner store tree)

Revision ID: 20260713_1801_runner_asks_and_parks
Revises: 20260713_1635_runner_heartbeats
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import asks, park_facts, park_resumes

revision: str = "20260713_1801_runner_asks_and_parks"
down_revision: str | None = "20260713_1635_runner_heartbeats"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (asks, park_facts, park_resumes)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
