"""transcript_outbound_buffer.kind (runner store tree, issue #246) — the fact-kind
discriminator (``transcript.delta`` | ``transcript.final``), mirroring ``outbound_buffer``.

Revision ID: 20260809_0900_runner_transcript_outbound_kind
Revises: 20260808_2300_runner_transcript_segments
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260809_0900_runner_transcript_outbound_kind"
down_revision: str | None = "20260808_2300_runner_transcript_segments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "transcript_outbound_buffer"
_COLUMN = "kind"
_BACKFILL = "transcript.delta"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(), nullable=False, server_default=_BACKFILL))


def downgrade() -> None:
    op.drop_column(_TABLE, _COLUMN)
