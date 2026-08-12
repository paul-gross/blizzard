"""The per-segment replacement unit and the standing convergence sweep (blizzard#254,
Phase 3).

There is no finalize hook to derive from (D1/D2): the sweep is the only first-derivation
path, and re-running it is the re-derive path — one engine, one convergence property.
Dependency-free (``bzh:domain-core``): :meth:`sweep` is one directly-callable step
(``bzh:steppable-loop``)."""

from __future__ import annotations

import json
from collections.abc import Sequence

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.hub.domain.analytics.events import IWriteTranscriptEvents, TranscriptEvent
from blizzard.hub.domain.analytics.extraction import (
    DEFAULT_EXTRACTORS,
    EXTRACTOR_VERSION,
    ITurnEventExtractor,
    extract_events,
)
from blizzard.hub.domain.work import IReadChunkRepository

_log = get_logger("blizzard.hub.transcript_events")


class EventDerivationService:
    """The per-segment replacement unit (D6) and the candidate-set predicate (D1).

    ``chunks`` resolves a node-step's ``graph_id`` (D4): the latest matching
    ``transitions`` row where one exists, else the chunk's own mint pin."""

    def __init__(
        self,
        *,
        events: IWriteTranscriptEvents,
        chunks: IReadChunkRepository,
        clock: IClock,
        extractors: Sequence[ITurnEventExtractor] = DEFAULT_EXTRACTORS,
        extractor_version: str = EXTRACTOR_VERSION,
    ) -> None:
        self._events = events
        self._chunks = chunks
        self._clock = clock
        self._extractors = extractors
        self._extractor_version = extractor_version

    def candidate_segment_ids(self, *, chunk_id: str | None = None) -> list[str]:
        """Every visible segment (D1) lacking a current-version marker, or whose marker
        disagrees with the segment's stored content today — the two are not the same
        set, and only the (unscoped) visible set governs which segments' rows the
        reconciler keeps. ``chunk_id`` narrows the visible set for the re-derive route's
        chunk-scoped call (D7); the standing reconciler never passes it."""
        candidates: list[str] = []
        for segment_id in self._events.visible_segment_ids(chunk_id=chunk_id):
            marker = self._events.derivation_marker(segment_id, self._extractor_version)
            if marker is None:
                candidates.append(segment_id)
                continue
            current = self._events.segment_derivation_input(segment_id)
            if current is None or current.content_fingerprint != marker.content_fingerprint:
                candidates.append(segment_id)
        return candidates

    def derive_segment(self, segment_id: str) -> None:
        """One transaction: recognize every event this segment's turns hold today,
        stamp the node-step context, and replace this ``(segment_id, extractor_version)``
        pair's rows and marker (D6). A no-longer-existing segment is a no-op — the
        reconciler's own drop path (D1) is what removes a superseded segment's rows."""
        current = self._events.segment_derivation_input(segment_id)
        if current is None:
            return
        graph_id = self._resolve_graph_id(current.chunk_id, current.node_id, current.epoch)
        extracted = extract_events(
            current.turns, normalizer_version=current.normalizer_version, extractors=self._extractors
        )
        events = [
            TranscriptEvent(
                kind=event.kind,
                turn_path=event.turn_path,
                occurrence=event.occurrence,
                payload=json.dumps(event.payload, sort_keys=True),
                chunk_id=current.chunk_id,
                node_id=current.node_id,
                epoch=current.epoch,
                spawn_generation=current.spawn_generation,
                graph_id=graph_id,
                depth=event.depth,
                agent_type=event.agent_type,
                occurred_at=event.occurred_at,
            )
            for event in extracted
        ]
        self._events.replace_segment_events(
            segment_id,
            self._extractor_version,
            events,
            complete=current.complete,
            content_fingerprint=current.content_fingerprint,
            at=self._clock.now(),
        )

    def _resolve_graph_id(self, chunk_id: str, node_id: str, epoch: int) -> str:
        facts = self._chunks.load_facts(chunk_id)
        matches = [
            t
            for t in (facts.transitions if facts is not None else [])
            if t.to_node_id == node_id and t.epoch == epoch and t.graph_id is not None
        ]
        if matches:
            newest = max(matches, key=lambda t: t.recorded_at)
            assert newest.graph_id is not None  # narrowed by the filter above
            return newest.graph_id
        chunk = self._chunks.get(chunk_id)
        assert chunk is not None, f"transcript segment references unknown chunk {chunk_id!r}"
        return chunk.graph_id


class EventDerivationReconciler:
    """The standing convergence pass (D1/D2), stepped by the existing ``Sweep`` driver.
    Derives each candidate through :class:`EventDerivationService`, then drops the rows
    of any segment the store still remembers but the visible set no longer holds."""

    def __init__(self, *, service: EventDerivationService, events: IWriteTranscriptEvents) -> None:
        self._service = service
        self._events = events

    def sweep(self) -> None:
        derived = 0
        for segment_id in self._service.candidate_segment_ids():
            self._service.derive_segment(segment_id)
            derived += 1

        visible = self._events.visible_segment_ids()
        dropped = 0
        for segment_id in self._events.derived_segment_ids() - visible:
            self._events.drop_segment(segment_id)
            dropped += 1

        _log.info("transcript event derivation sweep completed", derived=derived, dropped=dropped)
