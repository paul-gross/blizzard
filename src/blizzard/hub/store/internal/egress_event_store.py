"""SQLAlchemy adapter for the ``events`` egress dataset's reads (package-private).

A read-only projection of the derivation seam's tables — ``transcript_event_derivations``, ``transcript_events``,
``transcript_event_drops`` and ``transcript_segments`` — plus ``lease_facts`` for the backfill's epoch selection.
It writes nothing: :class:`~blizzard.hub.store.internal.transcript_event_store.TranscriptEventStore` owns them."""

from __future__ import annotations

import itertools
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Select, and_, or_, select

from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.observability.analytics.events import (
    DerivationMarker,
    DropFact,
    SegmentProvenance,
    TranscriptEvent,
)
from blizzard.hub.domain.observability.egress.event_rows import EventDerivation
from blizzard.hub.domain.observability.egress.repository import EpochKey, EventsPosition, IReadEgressEvents
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections

_d = s.transcript_event_derivations.c
_e = s.transcript_events.c
_x = s.transcript_event_drops.c
_g = s.transcript_segments.c
_l = s.lease_facts.c


def _markers_after_stmt(
    position: EventsPosition, until: datetime, limit: int, extractor_version: str | None
) -> Select[Any]:
    past = or_(
        _d.derived_at > position.at,
        and_(
            _d.derived_at == position.at,
            or_(
                _d.segment_id > position.segment_id,
                and_(_d.segment_id == position.segment_id, _d.extractor_version > position.extractor_version),
            ),
        ),
    )
    stmt = select(s.transcript_event_derivations).where(past, _d.derived_at <= until)
    if extractor_version is not None:
        stmt = stmt.where(_d.extractor_version == extractor_version)
    return stmt.order_by(_d.derived_at, _d.segment_id, _d.extractor_version).limit(limit)


def _drops_after_stmt(position: EventsPosition, until: datetime, limit: int) -> Select[Any]:
    # A drop's version is empty, so one at the position's own time and segment is never past it.
    past = or_(_x.dropped_at > position.at, and_(_x.dropped_at == position.at, _x.segment_id > position.segment_id))
    return (
        select(s.transcript_event_drops)
        .where(past, _x.dropped_at <= until)
        .order_by(_x.dropped_at, _x.segment_id, _x.id)
        .limit(limit)
    )


def _events_stmt(segment_ids: Sequence[str], versions: Sequence[str]) -> Select[Any]:
    return (
        select(s.transcript_events)
        .where(_e.segment_id.in_(segment_ids), _e.extractor_version.in_(versions))
        .order_by(_e.segment_id, _e.extractor_version, _e.id)
    )


def _segment_contexts_stmt(segment_ids: Sequence[str]) -> Select[Any]:
    """The columns a derivation's identity needs, never ``content``; ordered so a segment's first record leads."""
    return (
        select(
            _g.segment_id,
            _g.chunk_id,
            _g.epoch,
            _g.spawn_generation,
            _g.spawn_cwd,
            _g.harness_id,
            _g.harness_version,
            _g.model,
            _g.effort,
        )
        .where(_g.segment_id.in_(segment_ids))
        .order_by(_g.segment_id, _g.turn_range_start)
    )


def _marker_times_stmt(segment_ids: Sequence[str]) -> Select[Any]:
    return select(_d.segment_id, _d.extractor_version, _d.derived_at).where(_d.segment_id.in_(segment_ids))


def _epochs_minted_stmt(since: datetime, until: datetime, after: EpochKey | None, limit: int) -> Select[Any]:
    stmt = select(_l.chunk_id, _l.epoch).where(_l.minted_at >= since, _l.minted_at < until)
    if after is not None:
        stmt = stmt.where(
            or_(_l.chunk_id > after.chunk_id, and_(_l.chunk_id == after.chunk_id, _l.epoch > after.epoch))
        )
    return stmt.distinct().order_by(_l.chunk_id, _l.epoch).limit(limit)


def _epoch_markers_stmt(chunk_ids: Sequence[str], extractor_version: str | None) -> Select[Any]:
    stmt = (
        select(s.transcript_event_derivations, _g.chunk_id, _g.epoch)
        .join(s.transcript_segments, _g.segment_id == _d.segment_id)
        .where(_g.chunk_id.in_(chunk_ids))
    )
    if extractor_version is not None:
        stmt = stmt.where(_d.extractor_version == extractor_version)
    return stmt.distinct().order_by(_d.segment_id, _d.extractor_version)


def _epoch_drops_stmt(chunk_ids: Sequence[str]) -> Select[Any]:
    return (
        select(s.transcript_event_drops).where(_x.chunk_id.in_(chunk_ids)).order_by(_x.dropped_at, _x.segment_id, _x.id)
    )


def _marker(row: Any) -> DerivationMarker:
    return DerivationMarker(
        segment_id=row.segment_id,
        extractor_version=row.extractor_version,
        content_fingerprint=row.content_fingerprint,
        derived_at=row.derived_at,
        event_count=row.event_count,
        complete=row.complete,
    )


def _drop(row: Any) -> DropFact:
    return DropFact(
        segment_id=row.segment_id,
        chunk_id=row.chunk_id,
        epoch=row.epoch,
        spawn_generation=row.spawn_generation,
        dropped_at=row.dropped_at,
    )


def _event(row: Any) -> TranscriptEvent:
    return TranscriptEvent(
        kind=row.kind,
        turn_path=row.turn_path,
        occurrence=row.occurrence,
        payload=row.payload,
        subject=row.subject,
        tool=row.tool,
        chunk_id=row.chunk_id,
        node_id=row.node_id,
        epoch=row.epoch,
        spawn_generation=row.spawn_generation,
        graph_id=row.graph_id,
        depth=row.depth,
        agent_type=row.agent_type,
        occurred_at=row.occurred_at,
    )


def _provenance(events: Sequence[Any], records: Sequence[Any]) -> SegmentProvenance:
    """As derived: stamped on the events when there are any; otherwise the segment's own, by the derivation's rule
    — the first record's identity and the last known ``harness_version``."""
    if events:
        first = events[0]
        return SegmentProvenance(first.harness_id, first.harness_version, first.model, first.effort)
    first = records[0]
    versions = [record.harness_version for record in records if record.harness_version is not None]
    return SegmentProvenance(first.harness_id, versions[-1] if versions else None, first.model, first.effort)


class EgressEventStore:
    """The ``events`` export's reads."""

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    def markers_after(
        self, position: EventsPosition, until: datetime, limit: int, *, extractor_version: str | None
    ) -> Sequence[DerivationMarker]:
        with self._store.read("egress_markers_after") as conn:
            rows = conn.execute(_markers_after_stmt(position, until, limit, extractor_version)).all()
        return [_marker(row) for row in rows]

    def drops_after(self, position: EventsPosition, until: datetime, limit: int) -> Sequence[DropFact]:
        with self._store.read("egress_drops_after") as conn:
            rows = conn.execute(_drops_after_stmt(position, until, limit)).all()
        return [_drop(row) for row in rows]

    def derivations(self, markers: Sequence[DerivationMarker]) -> Sequence[EventDerivation]:
        result: list[EventDerivation] = []
        with self._store.read("egress_derivations") as conn:
            for batch in id_batches(list(markers)):
                segment_ids = sorted({marker.segment_id for marker in batch})
                versions = sorted({marker.extractor_version for marker in batch})
                events: dict[tuple[str, str], list[Any]] = defaultdict(list)
                for row in conn.execute(_events_stmt(segment_ids, versions)):
                    events[(row.segment_id, row.extractor_version)].append(row)
                records = {
                    segment_id: list(group)
                    for segment_id, group in itertools.groupby(
                        conn.execute(_segment_contexts_stmt(segment_ids)).all(), key=lambda row: row.segment_id
                    )
                }
                # Read after the events: a re-derive or drop committed since the marker was selected shows here.
                standing = {
                    (row.segment_id, row.extractor_version): row.derived_at
                    for row in conn.execute(_marker_times_stmt(segment_ids))
                }
                for marker in batch:
                    key = (marker.segment_id, marker.extractor_version)
                    segment = records.get(marker.segment_id)
                    if standing.get(key) != marker.derived_at or not segment:
                        continue
                    held = events.get(key, [])
                    first = segment[0]
                    result.append(
                        EventDerivation(
                            marker=marker,
                            provenance=_provenance(held, segment),
                            events=tuple(_event(row) for row in held),
                            chunk_id=first.chunk_id,
                            epoch=first.epoch,
                            spawn_generation=first.spawn_generation,
                            spawn_cwd=first.spawn_cwd,
                        )
                    )
        return result

    def epochs_minted_between(
        self, since: datetime, until: datetime, after: EpochKey | None, limit: int
    ) -> Sequence[EpochKey]:
        with self._store.read("egress_epochs_minted_between") as conn:
            rows = conn.execute(_epochs_minted_stmt(since, until, after, limit)).all()
        return [EpochKey(row.chunk_id, row.epoch) for row in rows]

    def epoch_markers(self, epochs: Sequence[EpochKey], *, extractor_version: str | None) -> Sequence[DerivationMarker]:
        wanted = set(epochs)
        markers: dict[tuple[str, str], DerivationMarker] = {}
        with self._store.read("egress_epoch_markers") as conn:
            for batch in id_batches(sorted({epoch.chunk_id for epoch in epochs})):
                for row in conn.execute(_epoch_markers_stmt(batch, extractor_version)):
                    if EpochKey(row.chunk_id, row.epoch) in wanted:
                        markers[(row.segment_id, row.extractor_version)] = _marker(row)
        return [markers[key] for key in sorted(markers)]

    def epoch_drops(self, epochs: Sequence[EpochKey]) -> Sequence[DropFact]:
        wanted = set(epochs)
        drops: list[DropFact] = []
        with self._store.read("egress_epoch_drops") as conn:
            for batch in id_batches(sorted({epoch.chunk_id for epoch in epochs})):
                drops.extend(
                    _drop(row)
                    for row in conn.execute(_epoch_drops_stmt(batch))
                    if EpochKey(row.chunk_id, row.epoch) in wanted
                )
        return sorted(drops, key=lambda drop: (drop.dropped_at, drop.segment_id))


def _conforms_egress_event_store(x: EgressEventStore) -> IReadEgressEvents:
    return x
