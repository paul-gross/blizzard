"""Freeze the resolved model/effort pair onto each transcript segment (blizzard#439).

Two guarded, nullable columns — un-backfilled, so NULL declares unknown, never a value.
Revision ID: 20260919_1000_transcript_segment_provenance
Revises: 20260918_1000_invocation_boundaries
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260919_1000_transcript_segment_provenance"
down_revision = "20260918_1000_invocation_boundaries"
branch_labels = None
depends_on = None

_TABLE = "transcript_segments"
_COLUMNS = ("model", "effort")


def _columns(bind: sa.Connection) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    existing = _columns(bind)
    for name in _COLUMNS:
        if name not in existing:
            op.add_column(_TABLE, sa.Column(name, sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing = _columns(bind)
    for name in _COLUMNS:
        if name in existing:
            with op.batch_alter_table(_TABLE) as batch:
                batch.drop_column(name)
