"""SQLAlchemy adapter for the transcript segment ledger repository seam (package-private,
blizzard#410)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, func, select

from blizzard.foundation.ids import SEGMENT_PREFIX, Id
from blizzard.foundation.logging import get_logger
from blizzard.runner.store.internal.base import (
    NO_NORMALIZER_VERSION,
    RunnerStoreConnections,
    enqueue_transcript_final,
    lease_select,
)
from blizzard.runner.store.schema import leases, transcript_outbound_buffer, transcript_segments
from blizzard.runner.transcripts.ledger import (
    BufferedTranscriptDelta,
    IWriteTranscriptLedgerRepository,
    TranscriptBackfillLease,
    TranscriptSegmentLedgerRow,
)

_log = get_logger("blizzard.runner.store")


class TranscriptLedgerStore:
    """Read-write transcript segment ledger adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    # --- reads --------------------------------------------------------------

    def transcript_segment(self, segment_id: str) -> TranscriptSegmentLedgerRow | None:
        rows = self._store.all(select(transcript_segments).where(transcript_segments.c.segment_id == segment_id))
        return self._row_to_transcript_segment(rows[0]) if rows else None

    def open_transcript_segments(self) -> list[TranscriptSegmentLedgerRow]:
        stmt = (
            select(transcript_segments)
            .where(transcript_segments.c.finalized_at.is_(None))
            .order_by(transcript_segments.c.segment_id)
        )
        return [self._row_to_transcript_segment(r) for r in self._store.all(stmt)]

    def transcript_segments_for_chunk(self, chunk_id: str) -> list[TranscriptSegmentLedgerRow]:
        stmt = (
            select(transcript_segments)
            .where(transcript_segments.c.chunk_id == chunk_id)
            .order_by(transcript_segments.c.stamped_at, transcript_segments.c.segment_id)
        )
        return [self._row_to_transcript_segment(r) for r in self._store.all(stmt)]

    def chunk_transcript_shipped_bytes(self, chunk_id: str) -> int:
        stmt = select(func.coalesce(func.sum(transcript_segments.c.shipped_bytes), 0)).where(
            transcript_segments.c.chunk_id == chunk_id
        )
        with self._store.connect() as conn:
            return int(conn.execute(stmt).scalar_one())

    def outstanding_transcript_buffer_bytes(self) -> int:
        # Payloads are `json.dumps(ensure_ascii=True)`, so SQL `length()` (chars) agrees
        # with the encoded byte length here (same fact the pump's own `_byte_cost` relies on).
        stmt = select(func.coalesce(func.sum(func.length(transcript_outbound_buffer.c.payload)), 0)).where(
            transcript_outbound_buffer.c.acked_at.is_(None)
        )
        with self._store.connect() as conn:
            return int(conn.execute(stmt).scalar_one())

    def has_unshipped_transcript_content(self, chunk_id: str) -> bool:
        stmt = (
            select(transcript_outbound_buffer.c.seq)
            .where(transcript_outbound_buffer.c.chunk_id == chunk_id)
            .where(transcript_outbound_buffer.c.acked_at.is_(None))
            .where(transcript_outbound_buffer.c.final.is_(False))
            .limit(1)
        )
        return bool(self._store.all(stmt))

    def pending_transcript_outbound(self, *, limit: int | None = None) -> list[BufferedTranscriptDelta]:
        stmt = (
            select(transcript_outbound_buffer)
            .where(transcript_outbound_buffer.c.acked_at.is_(None))
            .order_by(transcript_outbound_buffer.c.seq)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return [
            BufferedTranscriptDelta(
                seq=int(r.seq),
                final=bool(r.final),
                segment_id=str(r.segment_id),
                chunk_id=str(r.chunk_id),
                payload=str(r.payload),
                created_at=r.created_at,
            )
            for r in self._store.all(stmt)
        ]

    def transcript_backfill_leases(self) -> list[TranscriptBackfillLease]:
        # Correlated EXISTS on the session, not the lease: several leases share one
        # resumed session, and any segment for it means the session is already imported.
        has_segment = (
            select(transcript_segments.c.segment_id)
            .where(transcript_segments.c.session_id == leases.c.session_id)
            .exists()
        )
        stmt = (
            lease_select()
            .add_columns(has_segment.label("has_segment"))
            .where(leases.c.session_id.is_not(None))
            .order_by(leases.c.created_at, leases.c.lease_id)
        )
        return [
            TranscriptBackfillLease(
                lease_id=str(r.lease_id),
                chunk_id=str(r.chunk_id),
                node_id=str(r.node_id),
                epoch=int(r.epoch),
                session_id=str(r.session_id),
                has_segment=bool(r.has_segment),
            )
            for r in self._store.all(stmt)
        ]

    # --- writes -------------------------------------------------------------

    def mark_transcript_record_truncated(self, segment_id: str, *, reason: str, severity: int) -> bool:
        with self._store.begin() as conn:
            row = conn.execute(
                select(
                    transcript_segments.c.truncated_reason,
                    transcript_segments.c.truncated_reason_severity,
                    transcript_segments.c.truncated_reasons_warned,
                ).where(transcript_segments.c.segment_id == segment_id)
            ).first()
            if row is None:
                return False
            current_reason, current_severity, warned_json = row
            warned: list[str] = json.loads(warned_json) if warned_json is not None else []
            # Latched per (segment, reason) — a reason already warned never re-warns, no
            # matter how the display field below moves after it.
            already_warned = reason in warned
            values: dict[str, Any] = {}
            if not already_warned:
                values["truncated_reasons_warned"] = json.dumps([*warned, reason])
            # Worst-of, by the CALLER's own severity — the store holds no opinion on reasons.
            # `current_severity` is nullable and never backfilled: a row that took its reason
            # before that column existed reads NULL, which is not comparable.
            if current_reason != reason and (
                current_reason is None or current_severity is None or severity >= current_severity
            ):
                values["truncated_reason"] = reason
                values["truncated_reason_severity"] = severity
            if values:
                conn.execute(
                    transcript_segments.update().where(transcript_segments.c.segment_id == segment_id).values(**values)
                )
        changed = not already_warned
        if changed:
            _log.warning("transcript record truncated", segment_id=segment_id, reason=reason)
        return changed

    def stop_transcript_segment_shipping(self, segment_id: str, *, reason: str) -> bool:
        with self._store.begin() as conn:
            # `IS NULL` guard: a segment already stopped keeps its first reason.
            result = conn.execute(
                transcript_segments.update()
                .where(transcript_segments.c.segment_id == segment_id)
                .where(transcript_segments.c.shipping_stopped_reason.is_(None))
                .values(shipping_stopped_reason=reason)
            )
        changed = result.rowcount > 0
        if changed:
            _log.warning("transcript segment stopped shipping", segment_id=segment_id, reason=reason)
        return changed

    def mark_sidechain_dropped_warned(self, segment_id: str, *, agent_id: str | None) -> bool:
        with self._store.begin() as conn:
            row = conn.execute(
                select(transcript_segments.c.sidechain_warned_agents).where(
                    transcript_segments.c.segment_id == segment_id
                )
            ).first()
            warned: list[str | None] = json.loads(row[0]) if row is not None and row[0] is not None else []
            if agent_id in warned:
                return False
            warned.append(agent_id)
            conn.execute(
                transcript_segments.update()
                .where(transcript_segments.c.segment_id == segment_id)
                .values(sidechain_warned_agents=json.dumps(warned))
            )
        _log.warning("transcript segment dropped an unlinked sidechain", segment_id=segment_id, agent_id=agent_id)
        return True

    def record_transcript_deltas(
        self,
        *,
        segment_id: str,
        chunk_id: str,
        cursor: str | None,
        shipped_bytes: int,
        shipped_turns: int,
        normalizer_version: str,
        harness_version: str | None,
        payloads: list[str],
        created_at: datetime,
        agent_tool_use_ids: dict[str, str] | None = None,
    ) -> list[int]:
        with self._store.begin() as conn:
            conn.execute(
                transcript_segments.update()
                .where(transcript_segments.c.segment_id == segment_id)
                .values(
                    cursor=cursor,
                    shipped_bytes=shipped_bytes,
                    shipped_turns=shipped_turns,
                    normalizer_version=normalizer_version,
                    harness_version=harness_version,
                    **self._merged_agent_tool_use_ids(conn, segment_id, agent_tool_use_ids),
                )
            )
            seqs: list[int] = []
            for payload in payloads:
                result = conn.execute(
                    transcript_outbound_buffer.insert().values(
                        segment_id=segment_id,
                        chunk_id=chunk_id,
                        final=False,
                        payload=payload,
                        created_at=created_at,
                    )
                )
                key = result.inserted_primary_key
                seqs.append(int(key[0]) if key is not None else 0)
        return seqs

    def open_transcript_segment(
        self,
        *,
        chunk_id: str,
        node_id: str,
        epoch: int,
        generation: int,
        lease_id: str,
        session_id: str,
        stamped_at: datetime,
        supersedes: str | None = None,
    ) -> str:
        segment_id = Id.mint_at(SEGMENT_PREFIX, stamped_at).value
        with self._store.begin() as conn:
            conn.execute(
                transcript_segments.insert().values(
                    segment_id=segment_id,
                    chunk_id=chunk_id,
                    node_id=node_id,
                    epoch=epoch,
                    generation=generation,
                    lease_id=lease_id,
                    session_id=session_id,
                    cursor=None,
                    shipped_bytes=0,
                    shipped_turns=0,
                    normalizer_version=NO_NORMALIZER_VERSION,
                    harness_version=None,
                    truncated_reason=None,
                    shipping_stopped_reason=None,
                    supersedes=supersedes,
                    finalized_at=None,
                    stamped_at=stamped_at,
                )
            )
        _log.info("transcript segment opened", segment_id=segment_id, lease_id=lease_id, session_id=session_id)
        return segment_id

    def finalize_transcript_segment(self, segment_id: str, *, finalized_at: datetime) -> bool:
        with self._store.begin() as conn:
            # The open-only guard and the marker share this transaction, so a second call
            # can neither re-finalize nor enqueue a second final row.
            segment = conn.execute(
                select(transcript_segments)
                .where(transcript_segments.c.segment_id == segment_id)
                .where(transcript_segments.c.finalized_at.is_(None))
            ).one_or_none()
            if segment is None:
                return False
            conn.execute(
                transcript_segments.update()
                .where(transcript_segments.c.segment_id == segment_id)
                .values(finalized_at=finalized_at)
            )
            enqueue_transcript_final(conn, segment, at=finalized_at)
        _log.info("transcript segment finalized", segment_id=segment_id)
        return True

    def advance_transcript_cursor(
        self,
        segment_id: str,
        *,
        cursor: str,
        normalizer_version: str,
        harness_version: str | None,
        agent_tool_use_ids: dict[str, str] | None = None,
    ) -> None:
        with self._store.begin() as conn:
            conn.execute(
                transcript_segments.update()
                .where(transcript_segments.c.segment_id == segment_id)
                .values(
                    cursor=cursor,
                    normalizer_version=normalizer_version,
                    harness_version=harness_version,
                    **self._merged_agent_tool_use_ids(conn, segment_id, agent_tool_use_ids),
                )
            )

    def ack_transcript_outbound(self, seq: int, *, acked_at: datetime) -> None:
        with self._store.begin() as conn:
            # Non-final rows are pruned outright, nothing reading an acked one; a final
            # marker stays, acked in place — its row is the exactly-once receipt.
            conn.execute(
                transcript_outbound_buffer.delete()
                .where(transcript_outbound_buffer.c.seq == seq)
                .where(transcript_outbound_buffer.c.final.is_(False))
            )
            conn.execute(
                transcript_outbound_buffer.update()
                .where(transcript_outbound_buffer.c.seq == seq)
                .where(transcript_outbound_buffer.c.final.is_(True))
                .values(acked_at=acked_at)
            )

    # --- shared helpers -------------------------------------------------------

    @staticmethod
    def _row_to_transcript_segment(r) -> TranscriptSegmentLedgerRow:  # type: ignore[no-untyped-def]
        return TranscriptSegmentLedgerRow(
            segment_id=str(r.segment_id),
            chunk_id=str(r.chunk_id),
            node_id=str(r.node_id),
            epoch=int(r.epoch),
            generation=int(r.generation),
            lease_id=str(r.lease_id),
            session_id=str(r.session_id),
            cursor=str(r.cursor) if r.cursor is not None else None,
            shipped_bytes=int(r.shipped_bytes),
            shipped_turns=int(r.shipped_turns),
            normalizer_version=str(r.normalizer_version),
            harness_version=str(r.harness_version) if r.harness_version is not None else None,
            truncated_reason=str(r.truncated_reason) if r.truncated_reason is not None else None,
            shipping_stopped_reason=str(r.shipping_stopped_reason) if r.shipping_stopped_reason is not None else None,
            supersedes=str(r.supersedes) if r.supersedes is not None else None,
            finalized_at=r.finalized_at,
            stamped_at=r.stamped_at,
            agent_tool_use_ids=json.loads(r.agent_tool_use_ids) if r.agent_tool_use_ids else {},
        )

    @staticmethod
    def _merged_agent_tool_use_ids(conn: Connection, segment_id: str, learned: dict[str, str] | None) -> dict[str, str]:
        """The stored map merged with this window's pairs, as `values()` kwargs — empty when
        nothing was learned, so the column is left untouched rather than rewritten. Merged in
        the CALLER's transaction: a pair persisted without the cursor that read it re-learns
        nothing (blizzard#338)."""
        if not learned:
            return {}
        row = conn.execute(
            select(transcript_segments.c.agent_tool_use_ids).where(transcript_segments.c.segment_id == segment_id)
        ).first()
        stored: dict[str, str] = json.loads(row[0]) if row is not None and row[0] else {}
        # Stored wins: the first window to name a pair saw the `tool_result` that defines it,
        # and a later re-read of the same result must not renumber an established link.
        return {"agent_tool_use_ids": json.dumps({**learned, **stored})}


def _conforms_transcript_ledger_store(x: TranscriptLedgerStore) -> IWriteTranscriptLedgerRepository:
    return x
