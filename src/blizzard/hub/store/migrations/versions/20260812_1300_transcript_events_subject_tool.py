"""transcript_events.subject / transcript_events.tool (blizzard#255 D1) — payload's
filterable projection. Existing rows stay at their old extractor version until the sweep.

Revision ID: 20260812_1300_hub_transcript_events_subject_tool
Revises: 20260812_1200_hub_transcript_events
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260812_1300_hub_transcript_events_subject_tool"
down_revision: str | None = "20260812_1200_hub_transcript_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "transcript_events"
_COLUMNS = ("subject", "tool")
_INDEXES = {"subject": "ix_transcript_events_subject", "tool": "ix_transcript_events_tool"}


def _columns(bind: sa.Connection) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def _index_names(bind: sa.Connection) -> set[str | None]:
    return {i["name"] for i in sa.inspect(bind).get_indexes(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    existing = _columns(bind)
    for column in _COLUMNS:
        if column not in existing:
            op.add_column(_TABLE, sa.Column(column, sa.String(), nullable=True))
    existing_indexes = _index_names(bind)
    for column, index_name in _INDEXES.items():
        if index_name not in existing_indexes:
            op.create_index(index_name, _TABLE, [column])


def downgrade() -> None:
    bind = op.get_bind()
    existing_indexes = _index_names(bind)
    for index_name in reversed(list(_INDEXES.values())):
        if index_name in existing_indexes:
            op.drop_index(index_name, table_name=_TABLE)
    existing = _columns(bind)
    for column in reversed(_COLUMNS):
        if column in existing:
            op.drop_column(_TABLE, column)
