"""Harness provenance on hub-owned questions and transcript records.

Revision ID: 20260914_1000_hub_harness_provenance
Revises: 20260913_1300_hub_store_hot_path_indexes
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260914_1000_hub_harness_provenance"
down_revision: str | None = "20260913_1300_hub_store_hot_path_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_QUESTIONS = "questions"
_TRANSCRIPTS = "transcript_segments"
_HARNESS_ID = "harness_id"

# Frozen historical owner — this migration observes ownership only, never a version.
_CLAUDE_CODE = "claude_code"

# Narrow read/write stubs: these revisions add and backfill only these columns.
_metadata = sa.MetaData()
_questions = sa.Table(
    _QUESTIONS,
    _metadata,
    sa.Column("session_id", sa.String, nullable=True),
    sa.Column(_HARNESS_ID, sa.String, nullable=True),
)
_transcripts = sa.Table(
    _TRANSCRIPTS,
    _metadata,
    sa.Column(_HARNESS_ID, sa.String, nullable=True),
)


def _has_column(bind: sa.Connection, table: str) -> bool:
    return _HARNESS_ID in {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, _QUESTIONS):
        with op.batch_alter_table(_QUESTIONS) as batch:
            batch.add_column(sa.Column(_HARNESS_ID, sa.String, nullable=True))
        # Questions without a session have no concrete-session owner to backfill.
        bind.execute(_questions.update().where(_questions.c.session_id.is_not(None)).values(harness_id=_CLAUDE_CODE))
    if not _has_column(bind, _TRANSCRIPTS):
        with op.batch_alter_table(_TRANSCRIPTS) as batch:
            batch.add_column(sa.Column(_HARNESS_ID, sa.String, nullable=True))
        bind.execute(_transcripts.update().values(harness_id=_CLAUDE_CODE))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, _TRANSCRIPTS):
        with op.batch_alter_table(_TRANSCRIPTS) as batch:
            batch.drop_column(_HARNESS_ID)
    if _has_column(bind, _QUESTIONS):
        with op.batch_alter_table(_QUESTIONS) as batch:
            batch.drop_column(_HARNESS_ID)
