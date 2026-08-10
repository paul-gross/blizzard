"""SQLAlchemy adapter for the transcript-segment seam (package-private, blizzard#247).

All ``sqlalchemy`` and ``zlib`` usage is confined here (``bzh:dependency-inversion``) —
the domain hands this adapter plain turns JSON text and reads plain turns JSON text
back; compression (D10) is a storage detail the domain never sees."""

from __future__ import annotations

import itertools
import zlib
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, Insert, Select, Update, func, insert, select

from blizzard.hub.domain.transcripts import (
    IWriteTranscriptSegments,
    NaturalKeyState,
    SegmentIndexRow,
    SegmentRecord,
    SegmentRecordContent,
)
from blizzard.hub.store import schema as s

# --- statements -------------------------------------------------------------
# Every statement executed below comes from one of these builders and nowhere else, so
# the unit tier compiles the real ones under both dialects (`bzh:sql-portable`).


def _identity_values(record: SegmentRecord) -> dict[str, object]:
    return {
        "segment_id": record.segment_id,
        "chunk_id": record.chunk_id,
        "node_id": record.node_id,
        "epoch": record.epoch,
        "spawn_generation": record.spawn_generation,
        "runner_id": record.runner_id,
        "turn_range_start": record.turn_range_start,
        "turn_range_end": record.turn_range_end,
        "final": record.final,
        "normalizer_version": record.normalizer_version,
        "harness_version": record.harness_version,
        "record_truncated": record.record_truncated,
    }


def _segments_for_chunk_stmt(chunk_id: str) -> Select[Any]:
    return (
        select(s.transcript_segments)
        .where(s.transcript_segments.c.chunk_id == chunk_id)
        .order_by(s.transcript_segments.c.segment_id, s.transcript_segments.c.turn_range_start)
    )


def _records_for_segment_stmt(chunk_id: str, segment_id: str) -> Select[Any]:
    return (
        select(s.transcript_segments)
        .where(
            s.transcript_segments.c.chunk_id == chunk_id,
            s.transcript_segments.c.segment_id == segment_id,
        )
        .order_by(s.transcript_segments.c.turn_range_start)
    )


def _lease_runner_ids_stmt(chunk_id: str, node_id: str, epoch: int) -> Select[Any]:
    return (
        select(s.transcript_segments.c.runner_id)
        .where(
            s.transcript_segments.c.chunk_id == chunk_id,
            s.transcript_segments.c.node_id == node_id,
            s.transcript_segments.c.epoch == epoch,
        )
        .distinct()
    )


def _records_for_lease_stmt(chunk_id: str, node_id: str, epoch: int, runner_id: str) -> Select[Any]:
    return (
        select(s.transcript_segments)
        .where(
            s.transcript_segments.c.chunk_id == chunk_id,
            s.transcript_segments.c.node_id == node_id,
            s.transcript_segments.c.epoch == epoch,
            s.transcript_segments.c.runner_id == runner_id,
        )
        .order_by(
            s.transcript_segments.c.spawn_generation,
            s.transcript_segments.c.segment_id,
            s.transcript_segments.c.turn_range_start,
        )
    )


def _high_water_stmt(runner_id: str) -> Select[Any]:
    return select(s.transcript_high_water.c.seq).where(s.transcript_high_water.c.runner_id == runner_id)


def _high_water_owner_stmt(runner_id: str) -> Select[Any]:
    return select(s.transcript_high_water.c.runner_id).where(s.transcript_high_water.c.runner_id == runner_id)


def _natural_key_state_stmt(segment_id: str, turn_range_start: int) -> Select[Any]:
    return select(s.transcript_segments.c.rejected).where(
        s.transcript_segments.c.segment_id == segment_id,
        s.transcript_segments.c.turn_range_start == turn_range_start,
    )


def _chunk_stored_bytes_stmt(chunk_id: str) -> Select[Any]:
    return select(func.coalesce(func.sum(s.transcript_segments.c.byte_count), 0)).where(
        s.transcript_segments.c.chunk_id == chunk_id,
        s.transcript_segments.c.rejected.is_(False),
    )


def _runner_window_bytes_stmt(runner_id: str, since: datetime) -> Select[Any]:
    return select(func.coalesce(func.sum(s.transcript_segments.c.byte_count), 0)).where(
        s.transcript_segments.c.runner_id == runner_id,
        s.transcript_segments.c.received_at >= since,
    )


def _insert_high_water_stmt(runner_id: str, *, seq: int, at: datetime) -> Insert:
    return insert(s.transcript_high_water).values(runner_id=runner_id, seq=seq, updated_at=at)


def _update_high_water_stmt(runner_id: str, *, seq: int, at: datetime) -> Update:
    return (
        s.transcript_high_water.update()
        .where(s.transcript_high_water.c.runner_id == runner_id)
        .values(seq=seq, updated_at=at)
    )


def _insert_accepted_stmt(
    record: SegmentRecord, *, byte_count: int, codec: str, content: bytes, at: datetime
) -> Insert:
    return insert(s.transcript_segments).values(
        **_identity_values(record),
        rejected=False,
        rejection_reason=None,
        byte_count=byte_count,
        codec=codec,
        content=content,
        received_at=at,
    )


def _insert_rejected_stmt(record: SegmentRecord, *, byte_count: int, reason: str, at: datetime) -> Insert:
    return insert(s.transcript_segments).values(
        **_identity_values(record),
        rejected=True,
        rejection_reason=reason,
        byte_count=byte_count,
        codec=None,
        content=None,
        received_at=at,
    )


def _natural_key_rejected_row(record: SegmentRecord) -> Update:
    return s.transcript_segments.update().where(
        s.transcript_segments.c.segment_id == record.segment_id,
        s.transcript_segments.c.turn_range_start == record.turn_range_start,
        s.transcript_segments.c.rejected.is_(True),
    )


def _update_to_accepted_stmt(
    record: SegmentRecord, *, byte_count: int, codec: str, content: bytes, at: datetime
) -> Update:
    return _natural_key_rejected_row(record).values(
        rejected=False,
        rejection_reason=None,
        byte_count=byte_count,
        codec=codec,
        content=content,
        received_at=at,
        record_truncated=record.record_truncated,  # the re-offer's own value, not the first
    )


def _update_still_rejected_stmt(record: SegmentRecord, *, byte_count: int, reason: str, at: datetime) -> Update:
    return _natural_key_rejected_row(record).values(
        rejection_reason=reason,
        byte_count=byte_count,
        received_at=at,
        record_truncated=record.record_truncated,  # the re-offer's own value, not the first
    )


class TranscriptSegmentStore:
    """Read-write transcript-segment adapter over the hub store engine."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # --- reads ----------------------------------------------------------------

    def segments_for_chunk(self, chunk_id: str) -> list[SegmentIndexRow]:
        with self._engine.connect() as conn:
            rows = conn.execute(_segments_for_chunk_stmt(chunk_id)).all()
        return [
            self._index_row(segment_id, list(group))
            for segment_id, group in itertools.groupby(rows, key=lambda r: r.segment_id)
        ]

    def records_for_segment(self, chunk_id: str, segment_id: str) -> list[SegmentRecordContent]:
        with self._engine.connect() as conn:
            rows = conn.execute(_records_for_segment_stmt(chunk_id, segment_id)).all()
        return [
            SegmentRecordContent(
                turn_range_start=row.turn_range_start,
                turn_range_end=row.turn_range_end,
                final=row.final,
                rejected=row.rejected,
                record_truncated=bool(row.record_truncated),  # NULL (pre-column row) reads as False
                turns_json=self._decompress(row.content, row.codec) if row.content is not None else "[]",
            )
            for row in rows
        ]

    def runner_id_for_lease(self, chunk_id: str, node_id: str, epoch: int) -> str | None:
        """The single ``runner_id`` on a lease's stored segments (D2) — asserted, not
        assumed: a ``LIMIT 1`` with no ``ORDER BY`` would silently 403 the legitimate
        owner should two runners' rows ever share one key, so a violation raises here
        instead of picking an arbitrary row."""
        with self._engine.connect() as conn:
            rows = conn.execute(_lease_runner_ids_stmt(chunk_id, node_id, epoch)).all()
        if not rows:
            return None
        runner_ids = sorted({row.runner_id for row in rows})
        if len(runner_ids) > 1:
            raise RuntimeError(
                f"lease (chunk_id={chunk_id!r}, node_id={node_id!r}, epoch={epoch}) has segments "
                f"from multiple runners {runner_ids!r} — the fencing-epoch invariant this query "
                "depends on was violated"
            )
        return runner_ids[0]

    def records_for_lease(self, chunk_id: str, node_id: str, epoch: int, runner_id: str) -> list[SegmentRecordContent]:
        with self._engine.connect() as conn:
            rows = conn.execute(_records_for_lease_stmt(chunk_id, node_id, epoch, runner_id)).all()
        return [
            SegmentRecordContent(
                turn_range_start=row.turn_range_start,
                turn_range_end=row.turn_range_end,
                final=row.final,
                rejected=row.rejected,
                record_truncated=bool(row.record_truncated),  # NULL (pre-column row) reads as False
                turns_json=self._decompress(row.content, row.codec) if row.content is not None else "[]",
            )
            for row in rows
        ]

    def high_water(self, runner_id: str) -> int:
        with self._engine.connect() as conn:
            row = conn.execute(_high_water_stmt(runner_id)).one_or_none()
            return row.seq if row is not None else 0

    def natural_key_state(self, segment_id: str, turn_range_start: int) -> NaturalKeyState:
        with self._engine.connect() as conn:
            row = conn.execute(_natural_key_state_stmt(segment_id, turn_range_start)).one_or_none()
        if row is None:
            return "absent"
        return "rejected" if row.rejected else "accepted"

    def chunk_stored_bytes(self, chunk_id: str) -> int:
        with self._engine.connect() as conn:
            total = conn.execute(_chunk_stored_bytes_stmt(chunk_id)).scalar_one()
            return int(total)

    def runner_window_bytes(self, runner_id: str, *, since: datetime) -> int:
        with self._engine.connect() as conn:
            total = conn.execute(_runner_window_bytes_stmt(runner_id, since)).scalar_one()
            return int(total)

    # --- writes -----------------------------------------------------------------

    def set_high_water(self, runner_id: str, *, seq: int, at: datetime) -> None:
        with self._engine.begin() as conn:
            existing = conn.execute(_high_water_owner_stmt(runner_id)).one_or_none()
            if existing is None:
                conn.execute(_insert_high_water_stmt(runner_id, seq=seq, at=at))
                return
            conn.execute(_update_high_water_stmt(runner_id, seq=seq, at=at))

    def insert_accepted(self, record: SegmentRecord, *, byte_count: int, codec: str, at: datetime) -> None:
        content = self._compress(record.turns_json, codec)
        with self._engine.begin() as conn:
            conn.execute(_insert_accepted_stmt(record, byte_count=byte_count, codec=codec, content=content, at=at))

    def insert_rejected(self, record: SegmentRecord, *, byte_count: int, reason: str, at: datetime) -> None:
        with self._engine.begin() as conn:
            conn.execute(_insert_rejected_stmt(record, byte_count=byte_count, reason=reason, at=at))

    def update_to_accepted(self, record: SegmentRecord, *, byte_count: int, codec: str, at: datetime) -> None:
        content = self._compress(record.turns_json, codec)
        with self._engine.begin() as conn:
            conn.execute(_update_to_accepted_stmt(record, byte_count=byte_count, codec=codec, content=content, at=at))

    def update_still_rejected(self, record: SegmentRecord, *, byte_count: int, reason: str, at: datetime) -> None:
        with self._engine.begin() as conn:
            conn.execute(_update_still_rejected_stmt(record, byte_count=byte_count, reason=reason, at=at))

    # --- helpers ------------------------------------------------------------

    @staticmethod
    def _compress(turns_json: str, codec: str) -> bytes:
        assert codec == "zlib", codec  # the store's only codec today (D10)
        return zlib.compress(turns_json.encode("utf-8"))

    @staticmethod
    def _decompress(content: bytes, codec: str | None) -> str:
        assert codec == "zlib", codec  # the store's only codec today (D10)
        return zlib.decompress(content).decode("utf-8")

    @staticmethod
    def _index_row(segment_id: str, rows: list) -> SegmentIndexRow:  # type: ignore[type-arg]
        return SegmentIndexRow(
            segment_id=segment_id,
            node_id=rows[0].node_id,
            epoch=rows[0].epoch,
            spawn_generation=rows[0].spawn_generation,
            turn_range_start=min(r.turn_range_start for r in rows),
            turn_range_end=max(r.turn_range_end for r in rows),
            final=any(r.final for r in rows),
            # Cap-rejected (this hub) OR runner-declared `record_truncated`.
            truncated=any(r.rejected or bool(r.record_truncated) for r in rows),
            byte_count=sum(r.byte_count for r in rows),
            normalizer_version=rows[0].normalizer_version,
            harness_version=rows[0].harness_version,
            received_at=max(r.received_at for r in rows),
        )


def _conforms_transcript_segment_store(x: TranscriptSegmentStore) -> IWriteTranscriptSegments:
    return x
