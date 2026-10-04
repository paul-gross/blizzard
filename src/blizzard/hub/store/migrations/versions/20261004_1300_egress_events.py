"""The ``events`` egress dataset's reads and cursor: time-leading marker, lease-mint and drop indexes, and the
events position's ``segment_id``/``extractor_version`` on ``egress_cursor``.

Revision ID: 20261004_1300_egress_events
Revises: 20261004_1200_repository_records
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261004_1300_egress_events"
down_revision: str | None = "20261004_1200_repository_records"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CURSOR = "egress_cursor"
_CURSOR_COLUMNS = ("segment_id", "extractor_version")
_INDEXES: tuple[tuple[str, str, list[str]], ...] = (
    (
        "ix_transcript_event_derivations_derived_at_segment_id_extractor_version",
        "transcript_event_derivations",
        ["derived_at", "segment_id", "extractor_version"],
    ),
    ("ix_lease_facts_minted_at", "lease_facts", ["minted_at"]),
    ("ix_transcript_event_drops_chunk_id_epoch", "transcript_event_drops", ["chunk_id", "epoch"]),
)


def _index_names(bind: sa.Connection, table: str) -> set[str]:
    return {str(i["name"]) for i in sa.inspect(bind).get_indexes(table)}


def _columns(bind: sa.Connection) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_CURSOR)}


def upgrade() -> None:
    bind = op.get_bind()
    for name, table, columns in _INDEXES:
        if name not in _index_names(bind, table):
            op.create_index(name, table, columns)
    present = _columns(bind)
    for column in _CURSOR_COLUMNS:
        if column not in present:
            op.add_column(_CURSOR, sa.Column(column, sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    present = _columns(bind)
    dropping = [column for column in _CURSOR_COLUMNS if column in present]
    if dropping:
        with op.batch_alter_table(_CURSOR) as batch:
            for column in dropping:
                batch.drop_column(column)
    for name, table, _ in _INDEXES:
        if name in _index_names(bind, table):
            op.drop_index(name, table_name=table)
