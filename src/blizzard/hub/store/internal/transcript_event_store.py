"""SQLAlchemy adapter for the transcript-event seam (package-private).

Reads ``transcript_segments`` directly. All
``sqlalchemy``/``zlib``/``hashlib``/``json`` usage stays confined here (``bzh:dependency-inversion``)."""

from __future__ import annotations

import hashlib
import itertools
import json
import zlib
from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Delete, Insert, Row, Select, func

from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.observability.analytics.events import (
    CandidacyRead,
    DerivationMarker,
    DerivationSignature,
    IWriteTranscriptEvents,
    SegmentContext,
    SegmentDerivationInput,
    SegmentProvenance,
    TranscriptEvent,
    is_candidate,
    segment_complete,
)
from blizzard.hub.domain.observability.transcripts import TranscriptTurn
from blizzard.hub.store import schema as s
from blizzard.hub.store.scoped import ScopedConnections, ScopedStatements

# --- statements: nothing below executes a statement built elsewhere, so the unit tier
# compiles the real ones under both dialects (`bzh:sql-portable`). Every builder here takes
# the tenant's ``ScopedStatements``.

#: `drop_segments`'s own batch size — an unbounded `IN (...)` over a mass, simultaneous
#: visibility loss has no ceiling; this bounds each DELETE regardless of stale-set size.
_DROP_SEGMENTS_BATCH_SIZE = 1000


def _minted_chunk_ids_subselect(stmts: ScopedStatements) -> Select[Any]:
    """Every chunk id the ``chunks`` table holds — the parent ``transcript_segments.chunk_id``
    declares a foreign key to. A subquery, not a ``_stmt`` builder: composed into one, never
    executed itself."""
    return stmts.select(s.chunks, s.chunks.c.chunk_id)


def visible_segment_ids_stmt(
    stmts: ScopedStatements, chunk_id: str | None = None, segment_id: str | None = None
) -> Select[Any]:
    """The tenant's visible segments; the superseded-pointer check is a correlated ``NOT EXISTS``
    over the same tenant's segments, since a segment id is runner-minted."""
    successor = s.transcript_segments.alias("successor")
    superseded = stmts.exists(successor, successor.c.supersedes == s.transcript_segments.c.segment_id)
    stmt = (
        stmts.select(s.transcript_segments, s.transcript_segments.c.segment_id)
        .where(s.transcript_segments.c.final.is_(True))
        .where(~superseded)
        .where(s.transcript_segments.c.chunk_id.in_(_minted_chunk_ids_subselect(stmts)))
        .distinct()
    )
    if chunk_id is not None:
        stmt = stmt.where(s.transcript_segments.c.chunk_id == chunk_id)
    if segment_id is not None:
        stmt = stmt.where(s.transcript_segments.c.segment_id == segment_id)
    return stmt


def derived_segment_ids_stmt(stmts: ScopedStatements) -> Select[Any]:
    return stmts.select(s.transcript_event_derivations, s.transcript_event_derivations.c.segment_id).distinct()


def candidacy_digests_stmt(stmts: ScopedStatements, chunk_id: str | None = None) -> Select[Any]:
    """Each visible segment's own ``content_digest`` and ``harness_version``
    — the latter folds per shipped window, so a still-rejected re-offer that only
    refreshes it still changes the fingerprint. No ``content`` column named — the
    candidacy read's whole point. Ordered so a segment's rows arrive in range order."""
    return (
        stmts.select(
            s.transcript_segments,
            s.transcript_segments.c.segment_id,
            s.transcript_segments.c.turn_range_start,
            s.transcript_segments.c.content_digest,
            s.transcript_segments.c.harness_version,
        )
        .where(s.transcript_segments.c.segment_id.in_(visible_segment_ids_stmt(stmts, chunk_id)))
        .order_by(s.transcript_segments.c.segment_id, s.transcript_segments.c.turn_range_start)
    )


def derivation_signature_stmt(stmts: ScopedStatements) -> Select[Any]:
    """The change probe's one cheap aggregate read: row count, max
    ``id``, and max ``received_at`` over ``transcript_segments``, plus the ``chunks`` row
    count as a scalar subquery, so the whole probe is one statement. No per-row read: a
    fixed number of aggregates regardless of corpus size."""
    chunk_count = stmts.select(s.chunks, func.count()).scalar_subquery()
    return stmts.select(
        s.transcript_segments,
        func.count(s.transcript_segments.c.id).label("segment_count"),
        func.max(s.transcript_segments.c.id).label("max_segment_id"),
        func.max(s.transcript_segments.c.received_at).label("max_received_at"),
        chunk_count.label("chunk_count"),
    )


def current_markers_stmt(stmts: ScopedStatements, extractor_version: str, chunk_id: str | None = None) -> Select[Any]:
    """The markers at ``extractor_version``, bounded by the same visible-segment set the digest
    read walks — so a one-chunk candidacy reads only that chunk's markers."""
    return stmts.select(
        s.transcript_event_derivations,
        s.transcript_event_derivations.c.segment_id,
        s.transcript_event_derivations.c.content_fingerprint,
    ).where(
        (s.transcript_event_derivations.c.extractor_version == extractor_version)
        & s.transcript_event_derivations.c.segment_id.in_(visible_segment_ids_stmt(stmts, chunk_id))
    )


def segment_records_stmt(stmts: ScopedStatements, segment_id: str) -> Select[Any]:
    return (
        stmts.select(s.transcript_segments)
        .where(s.transcript_segments.c.segment_id == segment_id)
        .order_by(s.transcript_segments.c.turn_range_start)
    )


def marker_stmt(stmts: ScopedStatements, segment_id: str, extractor_version: str) -> Select[Any]:
    return stmts.select(s.transcript_event_derivations).where(
        s.transcript_event_derivations.c.segment_id == segment_id,
        s.transcript_event_derivations.c.extractor_version == extractor_version,
    )


def markers_stmt(stmts: ScopedStatements, extractor_version: str) -> Select[Any]:
    return stmts.select(s.transcript_event_derivations).where(
        s.transcript_event_derivations.c.extractor_version == extractor_version
    )


def segment_records_for_ids_stmt(stmts: ScopedStatements, segment_ids: Sequence[str]) -> Select[Any]:
    return (
        stmts.select(s.transcript_segments)
        .where(s.transcript_segments.c.segment_id.in_(segment_ids))
        .order_by(s.transcript_segments.c.segment_id, s.transcript_segments.c.turn_range_start)
    )


def segment_contexts_stmt(stmts: ScopedStatements, segment_ids: Sequence[str]) -> Select[Any]:
    """``segment_records_for_ids_stmt``'s content-free sibling — every column
    :meth:`TranscriptEventStore.segment_contexts` needs, minus ``content``."""
    return (
        stmts.select(
            s.transcript_segments,
            s.transcript_segments.c.segment_id,
            s.transcript_segments.c.chunk_id,
            s.transcript_segments.c.node_id,
            s.transcript_segments.c.epoch,
            s.transcript_segments.c.spawn_generation,
            s.transcript_segments.c.normalizer_version,
            s.transcript_segments.c.harness_id,
            s.transcript_segments.c.harness_version,
            s.transcript_segments.c.model,
            s.transcript_segments.c.effort,
            s.transcript_segments.c.rejected,
            s.transcript_segments.c.content_digest,
        )
        .where(s.transcript_segments.c.segment_id.in_(segment_ids))
        .order_by(s.transcript_segments.c.segment_id, s.transcript_segments.c.turn_range_start)
    )


def delete_events_stmt(stmts: ScopedStatements, segment_id: str, extractor_version: str) -> Delete:
    return stmts.delete(s.transcript_events).where(
        s.transcript_events.c.segment_id == segment_id,
        s.transcript_events.c.extractor_version == extractor_version,
    )


def delete_marker_stmt(stmts: ScopedStatements, segment_id: str, extractor_version: str) -> Delete:
    return stmts.delete(s.transcript_event_derivations).where(
        s.transcript_event_derivations.c.segment_id == segment_id,
        s.transcript_event_derivations.c.extractor_version == extractor_version,
    )


def drop_identities_stmt(stmts: ScopedStatements, segment_ids: Sequence[str]) -> Select[Any]:
    """The chunk, epoch, and spawn generation a drop fact records — ordered so the first
    row per segment is the one :func:`segment_contexts_stmt` takes its context from."""
    return (
        stmts.select(
            s.transcript_segments,
            s.transcript_segments.c.segment_id,
            s.transcript_segments.c.chunk_id,
            s.transcript_segments.c.epoch,
            s.transcript_segments.c.spawn_generation,
        )
        .where(s.transcript_segments.c.segment_id.in_(segment_ids))
        .order_by(s.transcript_segments.c.segment_id, s.transcript_segments.c.turn_range_start)
    )


def insert_drops_stmt(stmts: ScopedStatements) -> Insert:
    """Executed once per batch with one parameter set per drop fact: the seam stamps the
    tenant into the statement, and a row never carries its own."""
    return stmts.insert(s.transcript_event_drops)


def insert_events_stmt(stmts: ScopedStatements) -> Insert:
    """Executed once per segment with :func:`event_rows` as its parameter sets."""
    return stmts.insert(s.transcript_events)


def event_rows(
    segment_id: str, extractor_version: str, events: list[TranscriptEvent], provenance: SegmentProvenance
) -> list[dict[str, Any]]:
    return [
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
            "harness_id": provenance.harness_id,
            "harness_version": provenance.harness_version,
            "model": provenance.model,
            "effort": provenance.effort,
        }
        for event in events
    ]


def upsert_marker_stmt(
    stmts: ScopedStatements,
    segment_id: str,
    extractor_version: str,
    *,
    content_fingerprint: str,
    event_count: int,
    complete: bool,
    at: datetime,
) -> Insert:
    return stmts.insert(s.transcript_event_derivations).values(
        segment_id=segment_id,
        extractor_version=extractor_version,
        content_fingerprint=content_fingerprint,
        derived_at=at,
        event_count=event_count,
        complete=complete,
    )


def delete_all_events_for_segments_stmt(stmts: ScopedStatements, segment_ids: Collection[str]) -> Delete:
    return stmts.delete(s.transcript_events).where(s.transcript_events.c.segment_id.in_(segment_ids))


def delete_all_markers_for_segments_stmt(stmts: ScopedStatements, segment_ids: Collection[str]) -> Delete:
    return stmts.delete(s.transcript_event_derivations).where(
        s.transcript_event_derivations.c.segment_id.in_(segment_ids)
    )


def fingerprint_from_parts(parts: Sequence[tuple[str, str | None]]) -> str:
    digest = hashlib.sha256()
    for content_digest_value, harness_version in parts:
        digest.update(content_digest_value.encode("ascii"))
        digest.update(b"\x00")
        digest.update((harness_version or "").encode("utf-8"))
        digest.update(b"\x01")
    return digest.hexdigest()


def content_fingerprint(records: Sequence[Any]) -> str:
    """A deterministic fingerprint of a segment's own content plus its own
    ``harness_version`` — content via each record's ``content_digest``,
    harness_version folded in too since a still-rejected re-offer can refresh it with no
    content change. Recomputing it never reads a content byte."""
    return fingerprint_from_parts([(row.content_digest, row.harness_version) for row in records])


def _provenance(rows: Sequence[Any]) -> SegmentProvenance:
    """A segment's frozen provenance: ``harness_id``/``model``/``effort``
    are identical on every stored record, so the first states them; ``harness_version`` is
    the last non-``None`` value over every row in ``turn_range_start`` order, so a later
    row's real value is never shadowed by an earlier ``None``."""
    first = rows[0]
    harness_version = first.harness_version
    for row in rows:
        if row.harness_version is not None:
            harness_version = row.harness_version
    return SegmentProvenance(
        harness_id=first.harness_id, harness_version=harness_version, model=first.model, effort=first.effort
    )


def _decode_turns(records: Sequence[Any]) -> list[TranscriptTurn]:
    """Every non-rejected record's turns, decompressed and concatenated in range order —
    (``bzh:domain-core``)."""
    turns: list[TranscriptTurn] = []
    for row in records:
        if row.rejected or row.content is None:
            continue
        assert row.codec == "zlib", row.codec  # the store's only codec today
        turns_json = zlib.decompress(row.content).decode("utf-8")
        turns.extend(TranscriptTurn.of_stored(turn) for turn in json.loads(turns_json))
    return turns


class TranscriptEventStore:
    """Read-write transcript-event adapter over the hub store engine."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    # --- reads ----------------------------------------------------------------

    def visible_segment_ids(self, *, chunk_id: str | None = None, segment_id: str | None = None) -> frozenset[str]:
        with self._store.read("visible_segment_ids") as conn:
            rows = conn.execute(visible_segment_ids_stmt(self._store, chunk_id, segment_id)).all()
        return frozenset(row.segment_id for row in rows)

    def derived_segment_ids(self) -> frozenset[str]:
        with self._store.read("derived_segment_ids") as conn:
            rows = conn.execute(derived_segment_ids_stmt(self._store)).all()
        return frozenset(row.segment_id for row in rows)

    def candidacy(self, extractor_version: str, *, chunk_id: str | None = None) -> CandidacyRead:
        with self._store.read("candidacy") as conn:
            digest_rows = conn.execute(candidacy_digests_stmt(self._store, chunk_id)).all()
            markers = {
                row.segment_id: row.content_fingerprint
                for row in conn.execute(current_markers_stmt(self._store, extractor_version, chunk_id))
            }
        visible_segment_ids: set[str] = set()
        candidates: list[str] = []
        for segment_id, rows in itertools.groupby(digest_rows, key=lambda r: r.segment_id):
            visible_segment_ids.add(segment_id)
            fingerprint = fingerprint_from_parts([(row.content_digest, row.harness_version) for row in rows])
            if is_candidate(markers.get(segment_id), fingerprint):
                candidates.append(segment_id)
        return CandidacyRead(visible_segment_ids=frozenset(visible_segment_ids), candidate_segment_ids=candidates)

    def derivation_signature(self) -> DerivationSignature:
        with self._store.read("derivation_signature") as conn:
            row = conn.execute(derivation_signature_stmt(self._store)).one()
        return DerivationSignature(
            segment_count=row.segment_count,
            max_segment_id=row.max_segment_id,
            max_received_at=row.max_received_at,
            chunk_count=row.chunk_count,
        )

    def segment_derivation_input(self, segment_id: str) -> SegmentDerivationInput | None:
        with self._store.read("segment_derivation_input") as conn:
            rows = conn.execute(segment_records_stmt(self._store, segment_id)).all()
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
            complete=segment_complete(row.rejected for row in rows),
            content_fingerprint=content_fingerprint(rows),
            provenance=_provenance(rows),
        )

    def derivation_marker(self, segment_id: str, extractor_version: str) -> DerivationMarker | None:
        with self._store.read("derivation_marker") as conn:
            row = conn.execute(marker_stmt(self._store, segment_id, extractor_version)).one_or_none()
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

    def derivation_markers(self, extractor_version: str) -> dict[str, DerivationMarker]:
        with self._store.read("derivation_markers") as conn:
            rows = conn.execute(markers_stmt(self._store, extractor_version)).all()
        return {
            row.segment_id: DerivationMarker(
                segment_id=row.segment_id,
                extractor_version=row.extractor_version,
                content_fingerprint=row.content_fingerprint,
                derived_at=row.derived_at,
                event_count=row.event_count,
                complete=row.complete,
            )
            for row in rows
        }

    def segment_derivation_inputs(self, segment_ids: Sequence[str]) -> dict[str, SegmentDerivationInput]:
        """`src/blizzard/hub/domain/observability/analytics/events.py`'s
        `IReadTranscriptEvents.segment_derivation_inputs`. A pure read, logging no drop
        (``bzh:dependency-inversion``)."""
        result: dict[str, SegmentDerivationInput] = {}
        if not segment_ids:
            return result
        with self._store.read("segment_derivation_inputs") as conn:
            for batch in id_batches(list(segment_ids)):
                rows = conn.execute(segment_records_for_ids_stmt(self._store, batch)).all()
                for segment_id, group in itertools.groupby(rows, key=lambda row: row.segment_id):
                    group_rows = list(group)
                    try:
                        result[segment_id] = SegmentDerivationInput(
                            segment_id=segment_id,
                            chunk_id=group_rows[0].chunk_id,
                            node_id=group_rows[0].node_id,
                            epoch=group_rows[0].epoch,
                            spawn_generation=group_rows[0].spawn_generation,
                            normalizer_version=group_rows[0].normalizer_version,
                            turns=_decode_turns(group_rows),
                            complete=not any(row.rejected for row in group_rows),
                            content_fingerprint=content_fingerprint(group_rows),
                            provenance=_provenance(group_rows),
                        )
                    except Exception:
                        continue  # decode failure: this id is dropped, unlike the singular's raise
        return result

    def segment_contexts(self, segment_ids: Sequence[str]) -> dict[str, SegmentContext]:
        result: dict[str, SegmentContext] = {}
        if not segment_ids:
            return result
        with self._store.read("segment_contexts") as conn:
            for batch in id_batches(list(segment_ids)):
                rows = conn.execute(segment_contexts_stmt(self._store, batch)).all()
                for segment_id, group in itertools.groupby(rows, key=lambda row: row.segment_id):
                    group_rows = list(group)
                    result[segment_id] = SegmentContext(
                        segment_id=segment_id,
                        chunk_id=group_rows[0].chunk_id,
                        node_id=group_rows[0].node_id,
                        epoch=group_rows[0].epoch,
                        spawn_generation=group_rows[0].spawn_generation,
                        normalizer_version=group_rows[0].normalizer_version,
                        complete=not any(row.rejected for row in group_rows),
                        content_fingerprint=content_fingerprint(group_rows),
                        provenance=_provenance(group_rows),
                    )
        return result

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
        provenance: SegmentProvenance,
    ) -> None:
        with self._store.write("replace_segment_events") as conn:
            conn.execute(delete_events_stmt(self._store, segment_id, extractor_version))
            conn.execute(delete_marker_stmt(self._store, segment_id, extractor_version))
            if events:
                conn.execute(
                    insert_events_stmt(self._store), event_rows(segment_id, extractor_version, events, provenance)
                )
            conn.execute(
                upsert_marker_stmt(
                    self._store,
                    segment_id,
                    extractor_version,
                    content_fingerprint=content_fingerprint,
                    event_count=len(events),
                    complete=complete,
                    at=at,
                )
            )

    def drop_segments(self, segment_ids: frozenset[str], *, at: datetime) -> None:
        if not segment_ids:
            return
        with self._store.write("drop_segments") as conn:
            for batch in itertools.batched(segment_ids, _DROP_SEGMENTS_BATCH_SIZE):
                # A segment with no stored record writes no drop fact: nothing to record it against.
                first_by_segment: dict[str, Row[Any]] = {}
                for row in conn.execute(drop_identities_stmt(self._store, batch)).all():
                    first_by_segment.setdefault(row.segment_id, row)
                if first_by_segment:
                    conn.execute(
                        insert_drops_stmt(self._store),
                        [
                            {
                                "segment_id": segment_id,
                                "chunk_id": row.chunk_id,
                                "epoch": row.epoch,
                                "spawn_generation": row.spawn_generation,
                                "dropped_at": at,
                            }
                            for segment_id, row in first_by_segment.items()
                        ],
                    )
                conn.execute(delete_all_events_for_segments_stmt(self._store, batch))
                conn.execute(delete_all_markers_for_segments_stmt(self._store, batch))


_transcript_event_store_conforms: type[IWriteTranscriptEvents] = TranscriptEventStore
