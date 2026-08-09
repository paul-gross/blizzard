"""transcript_segments.shipping_stopped_reason + a chunk_id index (runner store tree, issue #246)

Splits the per-chunk-budget stop-shipping latch out of ``truncated_reason``, which stays informational.
Revision ID: 20260809_1400_runner_transcript_shipping_stopped
Revises: 20260809_0900_runner_transcript_outbound_kind
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260809_1400_runner_transcript_shipping_stopped"
down_revision: str | None = "20260809_0900_runner_transcript_outbound_kind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "transcript_segments"
_COLUMN = "shipping_stopped_reason"
_INDEX = "ix_transcript_segments_chunk_id"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(), nullable=True))
    op.create_index(_INDEX, _TABLE, ["chunk_id"])


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_column(_TABLE, _COLUMN)
