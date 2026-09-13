"""Runner-store read indexes (issue #520): the heartbeat staleness probe, the outbound
buffer's pending-fact reads, per-lease attachments, `HELD_BINDING`, and the per-chunk
transcript-segment reads — plus `outbound_buffer`'s own `sqlite_autoincrement` fix,
mirroring `transcript_outbound_buffer`'s (blizzard-context:/standards/persistence.md).

Revision ID: 20260913_1100_runner_store_indexes
Revises: 20260905_1000_runner_external_usage_samples_slug
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260913_1100_runner_store_indexes"
down_revision: str | None = "20260905_1000_runner_external_usage_samples_slug"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (index name, table, columns) — created/dropped together, in this order forward and
# reversed on the way back.
_NEW_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ix_heartbeats_lease_id_beat_at", "heartbeats", ("lease_id", "beat_at")),
    ("ix_outbound_buffer_acked_at_seq", "outbound_buffer", ("acked_at", "seq")),
    ("ix_attachments_lease_id_name_id", "attachments", ("lease_id", "name", "id")),
    (
        "ix_binding_releases_chunk_id_environment_id_released_at",
        "binding_releases",
        ("chunk_id", "environment_id", "released_at"),
    ),
)

_OLD_CHUNK_INDEX = "ix_transcript_segments_chunk_id"
_NEW_CHUNK_INDEX = "ix_transcript_segments_chunk_id_stamped_at_segment_id"
_CHUNK_INDEX_TABLE = "transcript_segments"
_CHUNK_INDEX_COLUMNS = ("chunk_id", "stamped_at", "segment_id")


def _has_index(bind: sa.Connection, table: str, name: str) -> bool:
    return name in {i["name"] for i in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()

    # `outbound_buffer` never deletes a row (unlike `transcript_outbound_buffer`), so this
    # is purely for a fresh store's `sqlite_sequence` seed to match a migrated one
    # row-for-row — the batch recreate copies every row across with its explicit `seq`,
    # which seeds the sequence at the current max automatically.
    with op.batch_alter_table("outbound_buffer", recreate="always", table_kwargs={"sqlite_autoincrement": True}):
        pass

    for name, table, columns in _NEW_INDEXES:
        if not _has_index(bind, table, name):
            op.create_index(name, table, list(columns))

    if _has_index(bind, _CHUNK_INDEX_TABLE, _OLD_CHUNK_INDEX):
        op.drop_index(_OLD_CHUNK_INDEX, table_name=_CHUNK_INDEX_TABLE)
    if not _has_index(bind, _CHUNK_INDEX_TABLE, _NEW_CHUNK_INDEX):
        op.create_index(_NEW_CHUNK_INDEX, _CHUNK_INDEX_TABLE, list(_CHUNK_INDEX_COLUMNS))


def downgrade() -> None:
    bind = op.get_bind()

    if _has_index(bind, _CHUNK_INDEX_TABLE, _NEW_CHUNK_INDEX):
        op.drop_index(_NEW_CHUNK_INDEX, table_name=_CHUNK_INDEX_TABLE)
    if not _has_index(bind, _CHUNK_INDEX_TABLE, _OLD_CHUNK_INDEX):
        op.create_index(_OLD_CHUNK_INDEX, _CHUNK_INDEX_TABLE, ["chunk_id"])

    for name, table, _columns in reversed(_NEW_INDEXES):
        if _has_index(bind, table, name):
            op.drop_index(name, table_name=table)

    # Strips the AUTOINCREMENT keyword back off — `table_kwargs` defaults to none, and a
    # plain `INTEGER PRIMARY KEY` is what reflection restores everything else from.
    with op.batch_alter_table("outbound_buffer", recreate="always"):
        pass
