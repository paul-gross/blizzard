"""SQLAlchemy adapter for the transcript-event seam (package-private, blizzard#254).

Reads ``transcript_segments`` directly — the same table :mod:`transcript_segment_store`
adapts — rather than depending on that adapter: two ``internal/`` adapters sharing one
engine and schema module is established, not a coupling between them. All
``sqlalchemy``/``zlib``/``hashlib``/``json`` usage stays confined here (``bzh:dependency-inversion``)."""

from __future__ import annotations

import hashlib
import json
import zlib
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Delete, Insert, Select, insert, select

from blizzard.hub.domain.analytics.events import (
    DerivationMarker,
    IWriteTranscriptEvents,
    SegmentDerivationInput,
    TranscriptEvent,
)
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.wire.transcript_segment import TurnSegmentView

# --- statements: nothing below executes a statement built elsewhere, so the unit tier
# compiles the real ones under both dialects (`bzh:sql-portable`).


def _minted_chunk_ids_subselect() -> Select[Any]:
    """Every chunk id the ``chunks`` table holds — the parent ``transcript_segments.chunk_id``
    declares a foreign key to. A subquery, not a ``_stmt`` builder: composed into one, never
    executed itself."""
    return select(s.chunks.c.chunk_id)


def _visible_segment_ids_stmt(chunk_id: str | None = None) -> Select[Any]:
    superseded = select(s.transcript_segments.c.supersedes).where(s.transcript_segments.c.supersedes.is_not(None))
    stmt = (
        select(s.transcript_segments.c.segment_id)
        .where(s.transcript_segments.c.final.is_(True))
        .where(s.transcript_segments.c.segment_id.not_in(superseded))
        .where(s.transcript_segments.c.chunk_id.in_(_minted_chunk_ids_subselect()))
        .distinct()
    )
    if chunk_id is not None:
        stmt = stmt.where(s.transcript_segments.c.chunk_id == chunk_id)
    return stmt


def _derived_segment_ids_stmt() -> Select[Any]:
    return select(s.transcript_event_derivations.c.segment_id).distinct()


def _segment_records_stmt(segment_id: str) -> Select[Any]:
    return (
        select(s.transcript_segments)
        .where(s.transcript_segments.c.segment_id == segment_id)
        .order_by(s.transcript_segments.c.turn_range_start)
    )


def _marker_stmt(segment_id: str, extractor_version: str) -> Select[Any]:
    return select(s.transcript_event_derivations).where(
        s.transcript_event_derivations.c.segment_id == segment_id,
        s.transcript_event_derivations.c.extractor_version == extractor_version,
    )


def _delete_events_stmt(segment_id: str, extractor_version: str) -> Delete:
    return s.transcript_events.delete().where(
        s.transcript_events.c.segment_id == segment_id,
        s.transcript_events.c.extractor_version == extractor_version,
    )


def _delete_marker_stmt(segment_id: str, extractor_version: str) -> Delete:
    return s.transcript_event_derivations.delete().where(
        s.transcript_event_derivations.c.segment_id == segment_id,
        s.transcript_event_derivations.c.extractor_version == extractor_version,
    )


def _insert_events_stmt(segment_id: str, extractor_version: str, events: list[TranscriptEvent]) -> Insert:
    return insert(s.transcript_events).values(
        [
            {
                "segment_id": segment_id,
                "extractor_version": extractor_version,
                "kind": event.kind,
                "turn_path": event.turn_path,
                "occurrence": event.occurrence,
                "payload": event.payload,
                "subject": event.subject,
                "tool": event.tool,
                "chunk_id": event.chunk_id,
                "node_id": event.node_id,
                "epoch": event.epoch,
                "spawn_generation": event.spawn_generation,
                "graph_id": event.graph_id,
                "depth": event.depth,
                "agent_type": event.agent_type,
                "occurred_at": event.occurred_at,
            }
            for event in events
        ]
    )


def _upsert_marker_stmt(
    segment_id: str,
    extractor_version: str,
    *,
    content_fingerprint: str,
    event_count: int,
    complete: bool,
    at: datetime,
) -> Insert:
    return insert(s.transcript_event_derivations).values(
        segment_id=segment_id,
        extractor_version=extractor_version,
        content_fingerprint=content_fingerprint,
        derived_at=at,
        event_count=event_count,
        complete=complete,
    )


def _delete_all_events_for_segment_stmt(segment_id: str) -> Delete:
    return s.transcript_events.delete().where(s.transcript_events.c.segment_id == segment_id)


def _delete_all_markers_for_segment_stmt(segment_id: str) -> Delete:
    return s.transcript_event_derivations.delete().where(s.transcript_event_derivations.c.segment_id == segment_id)


def content_fingerprint(records: Sequence[Any]) -> str:
    """A deterministic fingerprint of a segment's stored content — every record's
    ``(turn_range_start, rejected, content)`` in range order — so the derivation marker
    can tell a later re-adjudication (a rejected record accepted, a late record landing)
    from an unchanged segment (D6)."""
    digest = hashlib.sha256()
    for row in records:
        digest.update(str(row.turn_range_start).encode("utf-8"))
        digest.update(b"\x00")
        digest.update(b"1" if row.rejected else b"0")
        digest.update(b"\x00")
        digest.update(row.content or b"")
        digest.update(b"\x01")
    return digest.hexdigest()


def _decode_turns(records: Sequence[Any]) -> list[TurnSegmentView]:
    """Every non-rejected record's turns, decompressed and concatenated in range order —
    the domain's own decode, independent of ``hub/api``'s (``bzh:domain-core``: this
    adapter owns its own reads, never reaches into an outer layer for them)."""
    turns: list[TurnSegmentView] = []
    for row in records:
        if row.rejected or row.content is None:
            continue
        assert row.codec == "zlib", row.codec  # the store's only codec today (D10)
        turns_json = zlib.decompress(row.content).decode("utf-8")
        turns.extend(TurnSegmentView.model_validate(turn) for turn in json.loads(turns_json))
    return turns


class TranscriptEventStore:
    """Read-write transcript-event adapter over the hub store engine."""

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    # --- reads ----------------------------------------------------------------

    def visible_segment_ids(self, *, chunk_id: str | None = None) -> frozenset[str]:
        with self._store.read("visible_segment_ids") as conn:
            rows = conn.execute(_visible_segment_ids_stmt(chunk_id)).all()
        return frozenset(row.segment_id for row in rows)

    def derived_segment_ids(self) -> frozenset[str]:
        with self._store.read("derived_segment_ids") as conn:
            rows = conn.execute(_derived_segment_ids_stmt()).all()
        return frozenset(row.segment_id for row in rows)

    def segment_derivation_input(self, segment_id: str) -> SegmentDerivationInput | None:
        with self._store.read("segment_derivation_input") as conn:
            rows = conn.execute(_segment_records_stmt(segment_id)).all()
        if not rows:
            return None
        first = rows[0]
        return SegmentDerivationInput(
            segment_id=segment_id,
            chunk_id=first.chunk_id,
            node_id=first.node_id,
            epoch=first.epoch,
            spawn_generation=first.spawn_generation,
            normalizer_version=first.normalizer_version,
            turns=_decode_turns(rows),
            complete=not any(row.rejected for row in rows),
            content_fingerprint=content_fingerprint(rows),
        )

    def derivation_marker(self, segment_id: str, extractor_version: str) -> DerivationMarker | None:
        with self._store.read("derivation_marker") as conn:
            row = conn.execute(_marker_stmt(segment_id, extractor_version)).one_or_none()
        if row is None:
            return None
        return DerivationMarker(
            segment_id=row.segment_id,
            extractor_version=row.extractor_version,
            content_fingerprint=row.content_fingerprint,
            derived_at=row.derived_at,
            event_count=row.event_count,
            complete=row.complete,
        )

    # --- writes -----------------------------------------------------------------

    def replace_segment_events(
        self,
        segment_id: str,
        extractor_version: str,
        events: list[TranscriptEvent],
        *,
        complete: bool,
        content_fingerprint: str,
        at: datetime,
    ) -> None:
        with self._store.write("replace_segment_events") as conn:
            conn.execute(_delete_events_stmt(segment_id, extractor_version))
            conn.execute(_delete_marker_stmt(segment_id, extractor_version))
            if events:
                conn.execute(_insert_events_stmt(segment_id, extractor_version, events))
            conn.execute(
                _upsert_marker_stmt(
                    segment_id,
                    extractor_version,
                    content_fingerprint=content_fingerprint,
                    event_count=len(events),
                    complete=complete,
                    at=at,
                )
            )

    def drop_segment(self, segment_id: str) -> None:
        with self._store.write("drop_segment") as conn:
            conn.execute(_delete_all_events_for_segment_stmt(segment_id))
            conn.execute(_delete_all_markers_for_segment_stmt(segment_id))


def _conforms_transcript_event_store(x: TranscriptEventStore) -> IWriteTranscriptEvents:
    return x
