"""The worker's working directory onto transcript_segments (runner store tree).

One guarded, nullable column — un-backfilled, so NULL declares unknown, never a value.
Revision ID: 20261003_1000_runner_transcript_segment_spawn_cwd
Revises: 20261002_1300_runner_lease_tokens_token_hash_index
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261003_1000_runner_transcript_segment_spawn_cwd"
down_revision: str | None = "20261002_1300_runner_lease_tokens_token_hash_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "transcript_segments"
_COLUMN = "spawn_cwd"


def _columns(bind: sa.Connection) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    if _COLUMN not in _columns(op.get_bind()):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(), nullable=True))


def downgrade() -> None:
    if _COLUMN in _columns(op.get_bind()):
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column(_COLUMN)
