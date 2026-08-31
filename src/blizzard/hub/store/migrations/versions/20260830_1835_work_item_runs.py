"""work_item_runs (blizzard#393 Phase 1 — a run's identity).

Revision ID: 20260830_1835_work_item_runs
Revises: 20260829_1930_fact_tables_chunk_id_index
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import work_item_runs

revision: str = "20260830_1835_work_item_runs"
down_revision: str | None = "20260829_1930_fact_tables_chunk_id_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    work_item_runs.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    work_item_runs.drop(bind, checkfirst=True)
