"""``transcript_segments.content_digest`` (blizzard#513 D1) — a per-record fingerprint of
``(turn_range_start, rejected, content)``, backfilled from raw stored bytes with no
decompression, so a later candidacy read never re-decodes content to detect a change.

Carries forward every marker whose stored ``content_fingerprint`` still matches the old
whole-segment formula recomputed from current rows, onto the new digest-based formula
(blizzard#513 D3) — a marker that doesn't match stays stale and is simply re-derived.

Revision ID: 20260913_1000_transcript_segments_content_digest
Revises: 20260907_1000_event_log_runner_id_nullable
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260913_1000_transcript_segments_content_digest"
down_revision: str | None = "20260907_1000_event_log_runner_id_nullable"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SEGMENTS_TABLE = "transcript_segments"

# Read/write-only references, not creates — the frozen shape is the narrow stub of
# columns this revision's selects and updates name (``bzh:frozen-revisions``). Never
# created, never dropped.
_frozen_metadata = sa.MetaData()

_segments = sa.Table(
    _SEGMENTS_TABLE,
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("segment_id", sa.String, nullable=False),
    sa.Column("turn_range_start", sa.Integer, nullable=False),
    sa.Column("rejected", sa.Boolean, nullable=False),
    sa.Column("content", sa.LargeBinary, nullable=True),
    # Added by this same revision's own `upgrade()`, just above — named here too so the
    # backfill below can build an `UPDATE ... SET content_digest = ...` against this table.
    sa.Column("content_digest", sa.String, nullable=True),
)

_derivations = sa.Table(
    "transcript_event_derivations",
    _frozen_metadata,
    sa.Column("segment_id", sa.String, primary_key=True),
    sa.Column("extractor_version", sa.String, primary_key=True),
    sa.Column("content_fingerprint", sa.String, nullable=False),
)

_FLUSH_EVERY = 1000


def _has_column(bind: sa.Connection) -> bool:
    return "content_digest" in {c["name"] for c in sa.inspect(bind).get_columns(_SEGMENTS_TABLE)}


def _row_digest(*, turn_range_start: int, rejected: bool, content: bytes | None) -> str:
    """Restates ``transcript_segment_store._row_content_digest`` as of this revision
    (``bzh:frozen-revisions``) — a future change to that function must not silently
    change what an already-migrated database backfilled."""
    digest = hashlib.sha256()
    digest.update(str(turn_range_start).encode("utf-8"))
    digest.update(b"\x00")
    digest.update(b"1" if rejected else b"0")
    digest.update(b"\x00")
    digest.update(content or b"")
    digest.update(b"\x01")
    return digest.hexdigest()


def _digests_fingerprint(digests: Sequence[str]) -> str:
    """Restates ``transcript_event_store.content_fingerprint``'s new, digest-based
    formula (``bzh:frozen-revisions``)."""
    digest = hashlib.sha256()
    for d in digests:
        digest.update(d.encode("ascii"))
        digest.update(b"\x01")
    return digest.hexdigest()


def _old_formula_fingerprint(rows: Sequence[sa.engine.Row]) -> str:
    """Restates the pre-D1 whole-segment formula this revision retires — one hash over
    every row's own ``(turn_range_start, rejected, content)`` in range order — so a
    still-valid marker can be told from a stale one before it is rewritten."""
    digest = hashlib.sha256()
    for row in rows:
        digest.update(str(row.turn_range_start).encode("utf-8"))
        digest.update(b"\x00")
        digest.update(b"1" if row.rejected else b"0")
        digest.update(b"\x00")
        digest.update(row.content or b"")
        digest.update(b"\x01")
    return digest.hexdigest()


def upgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind):
        return  # already reshaped — this revision's own guard, not per-row

    with op.batch_alter_table(_SEGMENTS_TABLE) as batch:
        batch.add_column(sa.Column("content_digest", sa.String, nullable=True))

    markers_by_segment: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for r in bind.execute(
        sa.select(_derivations.c.segment_id, _derivations.c.extractor_version, _derivations.c.content_fingerprint)
    ):
        markers_by_segment[r.segment_id].append((r.extractor_version, r.content_fingerprint))

    pending_row_updates: list[dict[str, object]] = []

    def flush_rows() -> None:
        if pending_row_updates:
            bind.execute(
                _segments.update()
                .where(_segments.c.id == sa.bindparam("row_id"))
                .values(content_digest=sa.bindparam("content_digest")),
                pending_row_updates,
            )
            pending_row_updates.clear()

    def process_segment(segment_id: str, rows: list) -> None:  # type: ignore[type-arg]
        rows_sorted = sorted(rows, key=lambda r: r.turn_range_start)
        digests = [
            _row_digest(turn_range_start=r.turn_range_start, rejected=r.rejected, content=r.content)
            for r in rows_sorted
        ]
        for row, d in zip(rows_sorted, digests, strict=True):
            pending_row_updates.append({"row_id": row.id, "content_digest": d})

        markers = markers_by_segment.get(segment_id)
        if not markers:
            return
        old_fp = _old_formula_fingerprint(rows_sorted)
        new_fp = _digests_fingerprint(digests)
        for extractor_version, stored_fp in markers:
            if stored_fp == old_fp:
                bind.execute(
                    _derivations.update()
                    .where(
                        _derivations.c.segment_id == segment_id,
                        _derivations.c.extractor_version == extractor_version,
                    )
                    .values(content_fingerprint=new_fp)
                )

    current_segment_id: str | None = None
    current_rows: list = []  # type: ignore[type-arg]
    rows_iter = bind.execute(
        sa.select(
            _segments.c.id,
            _segments.c.segment_id,
            _segments.c.turn_range_start,
            _segments.c.rejected,
            _segments.c.content,
        ).order_by(_segments.c.segment_id, _segments.c.turn_range_start)
    )
    for row in rows_iter:
        if current_segment_id is not None and row.segment_id != current_segment_id:
            process_segment(current_segment_id, current_rows)
            current_rows = []
            if len(pending_row_updates) >= _FLUSH_EVERY:
                flush_rows()
        current_segment_id = row.segment_id
        current_rows.append(row)
    if current_rows:
        process_segment(current_segment_id, current_rows)  # type: ignore[arg-type]
    flush_rows()

    with op.batch_alter_table(_SEGMENTS_TABLE) as batch:
        batch.alter_column("content_digest", nullable=False)


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        return  # already the pre-reshape shape
    # Markers are left as they are (D3): a stale one only costs a re-derive next pass.
    with op.batch_alter_table(_SEGMENTS_TABLE) as batch:
        batch.drop_column("content_digest")
