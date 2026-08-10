"""SQLAlchemy adapter for the transcript-segment seam (package-private, blizzard#247).

All ``sqlalchemy`` and ``zlib`` usage is confined here (``bzh:dependency-inversion``) —
the domain hands this adapter plain turns JSON text and reads plain turns JSON text
back; compression (D10) is a storage detail the domain never sees."""

from __future__ import annotations

import itertools
import zlib
from datetime import datetime

from sqlalchemy import Engine, func, insert, select

from blizzard.hub.domain.transcripts import (
    IWriteTranscriptSegments,
    NaturalKeyState,
    SegmentIndexRow,
    SegmentRecord,
    SegmentRecordContent,
)
from blizzard.hub.store import schema as s


class TranscriptSegmentStore:
    """Read-write transcript-segment adapter over the hub store engine."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # --- reads ----------------------------------------------------------------

    def segments_for_chunk(self, chunk_id: str) -> list[SegmentIndexRow]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(s.transcript_segments)
                .where(s.transcript_segments.c.chunk_id == chunk_id)
                .order_by(s.transcript_segments.c.segment_id, s.transcript_segments.c.turn_range_start)
            ).all()
        return [
            self._index_row(segment_id, list(group))
            for segment_id, group in itertools.groupby(rows, key=lambda r: r.segment_id)
        ]

    def records_for_segment(self, chunk_id: str, segment_id: str) -> list[SegmentRecordContent]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(s.transcript_segments)
                .where(
                    s.transcript_segments.c.chunk_id == chunk_id,
                    s.transcript_segments.c.segment_id == segment_id,
                )
                .order_by(s.transcript_segments.c.turn_range_start)
            ).all()
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
            row = conn.execute(
                select(s.transcript_high_water.c.seq).where(s.transcript_high_water.c.runner_id == runner_id)
            ).one_or_none()
            return row.seq if row is not None else 0

    def natural_key_state(self, segment_id: str, turn_range_start: int) -> NaturalKeyState:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(s.transcript_segments.c.rejected).where(
                    s.transcript_segments.c.segment_id == segment_id,
                    s.transcript_segments.c.turn_range_start == turn_range_start,
                )
            ).one_or_none()
        if row is None:
            return "absent"
        return "rejected" if row.rejected else "accepted"

    def chunk_stored_bytes(self, chunk_id: str) -> int:
        with self._engine.connect() as conn:
            total = conn.execute(
                select(func.coalesce(func.sum(s.transcript_segments.c.byte_count), 0)).where(
                    s.transcript_segments.c.chunk_id == chunk_id,
                    s.transcript_segments.c.rejected.is_(False),
                )
            ).scalar_one()
            return int(total)

    def runner_window_bytes(self, runner_id: str, *, since: datetime) -> int:
        with self._engine.connect() as conn:
            total = conn.execute(
                select(func.coalesce(func.sum(s.transcript_segments.c.byte_count), 0)).where(
                    s.transcript_segments.c.runner_id == runner_id,
                    s.transcript_segments.c.received_at >= since,
                )
            ).scalar_one()
            return int(total)

    # --- writes -----------------------------------------------------------------

    def set_high_water(self, runner_id: str, *, seq: int, at: datetime) -> None:
        with self._engine.begin() as conn:
            existing = conn.execute(
                select(s.transcript_high_water.c.runner_id).where(s.transcript_high_water.c.runner_id == runner_id)
            ).one_or_none()
            if existing is None:
                conn.execute(insert(s.transcript_high_water).values(runner_id=runner_id, seq=seq, updated_at=at))
                return
            conn.execute(
                s.transcript_high_water.update()
                .where(s.transcript_high_water.c.runner_id == runner_id)
                .values(seq=seq, updated_at=at)
            )

    def insert_accepted(self, record: SegmentRecord, *, byte_count: int, codec: str, at: datetime) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(s.transcript_segments).values(
                    **self._identity_values(record),
                    rejected=False,
                    rejection_reason=None,
                    byte_count=byte_count,
                    codec=codec,
                    content=self._compress(record.turns_json, codec),
                    received_at=at,
                )
            )

    def insert_rejected(self, record: SegmentRecord, *, byte_count: int, reason: str, at: datetime) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(s.transcript_segments).values(
                    **self._identity_values(record),
                    rejected=True,
                    rejection_reason=reason,
                    byte_count=byte_count,
                    codec=None,
                    content=None,
                    received_at=at,
                )
            )

    def update_to_accepted(self, record: SegmentRecord, *, byte_count: int, codec: str, at: datetime) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                s.transcript_segments.update()
                .where(
                    s.transcript_segments.c.segment_id == record.segment_id,
                    s.transcript_segments.c.turn_range_start == record.turn_range_start,
                    s.transcript_segments.c.rejected.is_(True),
                )
                .values(
                    rejected=False,
                    rejection_reason=None,
                    byte_count=byte_count,
                    codec=codec,
                    content=self._compress(record.turns_json, codec),
                    received_at=at,
                    record_truncated=record.record_truncated,  # review F10: not the first offer's, stale
                )
            )

    def update_still_rejected(self, record: SegmentRecord, *, byte_count: int, reason: str, at: datetime) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                s.transcript_segments.update()
                .where(
                    s.transcript_segments.c.segment_id == record.segment_id,
                    s.transcript_segments.c.turn_range_start == record.turn_range_start,
                    s.transcript_segments.c.rejected.is_(True),
                )
                .values(
                    rejection_reason=reason,
                    byte_count=byte_count,
                    received_at=at,
                    record_truncated=record.record_truncated,  # review F10: not the first offer's, stale
                )
            )

    # --- helpers ------------------------------------------------------------

    @staticmethod
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
            # Cap-rejected (this hub) OR runner-declared `record_truncated` (review F5).
            truncated=any(r.rejected or bool(r.record_truncated) for r in rows),
            byte_count=sum(r.byte_count for r in rows),
            normalizer_version=rows[0].normalizer_version,
            harness_version=rows[0].harness_version,
            received_at=max(r.received_at for r in rows),
        )


def _conforms_transcript_segment_store(x: TranscriptSegmentStore) -> IWriteTranscriptSegments:
    return x
