"""context samples — one append-only row per sampled reading of a running lease's session
context, the observation lane behind the configured warn line (runner store tree)

Revision ID: 20260811_1000_runner_context_samples
Revises: 20260811_0900_runner_transcript_segment_supersedes
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260811_1000_runner_context_samples"
down_revision: str | None = "20260811_0900_runner_transcript_segment_supersedes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_frozen_metadata = sa.MetaData()
_context_samples = sa.Table(
    "context_samples",
    _frozen_metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("lease_id", sa.String(), nullable=False, index=True),
    sa.Column("session_id", sa.String(), nullable=False),
    sa.Column("context_tokens", sa.Integer(), nullable=True),
    sa.Column("sampled_at", UtcDateTime(), nullable=False),
)
_TABLES = (_context_samples,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
