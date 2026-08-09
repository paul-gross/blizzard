"""transcript_outbound_buffer.kind (runner store tree, issue #246) — the fact-kind
discriminator (``transcript.delta`` | ``transcript.final``) the phase-1 revision omitted;
mirrors ``outbound_buffer.kind``. Backfills existing rows as ``transcript.delta`` (this
lane ships disabled by default, so no real row predates this revision).

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


def _has_column(bind: sa.Connection) -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(), nullable=False, server_default=_BACKFILL))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind):
        op.drop_column(_TABLE, _COLUMN)
