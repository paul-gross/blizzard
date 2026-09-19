"""Model/effort onto transcript_segments; harness id/version/model/effort onto transcript_events.

Six guarded, nullable columns — un-backfilled, so NULL declares unknown, never a value.
Revision ID: 20260919_1000_hub_transcript_provenance
Revises: 20260916_1100_hub_runner_capabilities
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260919_1000_hub_transcript_provenance"
down_revision: str | None = "20260916_1100_hub_runner_capabilities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS: dict[str, tuple[str, ...]] = {
    "transcript_segments": ("model", "effort"),
    "transcript_events": ("harness_id", "harness_version", "model", "effort"),
}


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    for table, names in _COLUMNS.items():
        existing = _columns(bind, table)
        for name in names:
            if name not in existing:
                op.add_column(table, sa.Column(name, sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    for table, names in _COLUMNS.items():
        existing = _columns(bind, table)
        for name in names:
            if name in existing:
                with op.batch_alter_table(table) as batch:
                    batch.drop_column(name)
