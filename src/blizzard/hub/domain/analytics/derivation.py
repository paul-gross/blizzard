"""The per-segment replacement unit and the standing convergence sweep (blizzard#254).

There is no finalize hook to derive from (D1/D2): the sweep is the only first-derivation
path, and re-running it is the re-derive path — one engine, one convergence property
(``bzh:domain-core``, ``bzh:steppable-loop``)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timedelta

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.hub.domain.analytics.events import (
    CandidacyRead,
    DerivationSignature,
    IWriteTranscriptEvents,
    TranscriptEvent,
)
from blizzard.hub.domain.analytics.extraction import (
    DEFAULT_EXTRACTORS,
    EXTRACTOR_VERSION,
    ITurnEventExtractor,
    extract_events,
)
from blizzard.hub.domain.chunks.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunks.record import IReadChunkRecordRepository

_log = get_logger("blizzard.hub.transcript_events")


class EventDerivationService:
    """The per-segment replacement unit (D6) and the candidate-set predicate (D1).

    ``facts``/``record`` resolve a node-step's ``graph_id`` (D4): the latest matching
    ``transitions`` row where one exists, else the chunk's own mint pin."""

    def __init__(
        self,
        *,
        events: IWriteTranscriptEvents,
        facts: IReadChunkFactsRepository,
        record: IReadChunkRecordRepository,
        clock: IClock,
        extractors: Sequence[ITurnEventExtractor] = DEFAULT_EXTRACTORS,
        extractor_version: str = EXTRACTOR_VERSION,
    ) -> None:
        self._events = events
        self._facts = facts
        self._record = record
        self._clock = clock
        self._extractors = extractors
        self._extractor_version = extractor_version

    def candidacy(self, *, chunk_id: str | None = None) -> CandidacyRead:
        """The pass's one visibility evaluation (D2): a bulk read of the visible set's
        stored digests against their current-version markers, with no content byte read
        and no statement issued per segment. ``chunk_id`` narrows it for the re-derive
        route's chunk-scoped call (D7); the standing reconciler never passes it."""
        return self._events.candidacy(self._extractor_version, chunk_id=chunk_id)

    def candidate_segment_ids(self, *, chunk_id: str | None = None) -> list[str]:
        """Every visible segment (D1) lacking a current-version marker, or whose marker
        disagrees with the segment's stored content today (D2) — the re-derive route's
        own entry point onto :meth:`candidacy`."""
        return self.candidacy(chunk_id=chunk_id).candidate_segment_ids

    def derive_segment(self, segment_id: str) -> bool:
        """One transaction: recognize every event this segment's turns hold today,
        stamp the node-step context, and replace this ``(segment_id, extractor_version)``
        pair's rows and marker (D6). Returns whether it derived: a segment that no longer
        exists, or whose ``chunk_id`` resolves to no chunk, is the no-op a caller must not
        count — the reconciler's drop path (D1) is what reclaims such a segment's rows."""
        current = self._events.segment_derivation_input(segment_id)
        if current is None:
            return False
        graph_id = self._resolve_graph_id(current.chunk_id, current.node_id, current.epoch)
        if graph_id is None:
            return False
        extracted = extract_events(
            current.turns, normalizer_version=current.normalizer_version, extractors=self._extractors
        )
        events = [
            TranscriptEvent(
                kind=event.kind,
                turn_path=event.turn_path,
                occurrence=event.occurrence,
                payload=json.dumps(event.payload, sort_keys=True),
                subject=event.subject,
                tool=event.tool,
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
        return True

    def _resolve_graph_id(self, chunk_id: str, node_id: str, epoch: int) -> str | None:
        """The node-step's graph (D4), or ``None`` when no chunk resolves — a segment
        whose ``chunk_id`` names none breaches the foreign key its table declares, so
        derivation declines it and ``hub:segment-chunk-resolves`` is what reports it."""
        facts = self._facts.load_facts(chunk_id)
        matches = [
            t
            for t in (facts.transitions if facts is not None else [])
            if t.to_node_id == node_id and t.epoch == epoch and t.graph_id is not None
        ]
        if matches:
            newest = max(matches, key=lambda t: t.recorded_at)
            assert newest.graph_id is not None  # narrowed by the filter above
            return newest.graph_id
        chunk = self._record.get(chunk_id)
        return chunk.graph_id if chunk is not None else None


#: The change probe's forced floor (blizzard#524 D5): an optimization only, so a full
#: pass still runs at least this often, bounding how stale a missed signature can leave reality.
_FORCED_FULL_PASS_FLOOR = timedelta(minutes=10)


class EventDerivationReconciler:
    """The standing convergence pass, stepped by the existing ``Sweep`` driver: derives
    each candidate, then drops rows for any segment no longer visible. Holds, in memory
    only, the last pass's :class:`~blizzard.hub.domain.analytics.events.DerivationSignature`;
    a fresh process always runs full, and :data:`_FORCED_FULL_PASS_FLOOR` bounds staleness."""

    def __init__(self, *, service: EventDerivationService, events: IWriteTranscriptEvents, clock: IClock) -> None:
        self._service = service
        self._events = events
        self._clock = clock
        self._last_signature: DerivationSignature | None = None
        self._last_full_pass_at: datetime | None = None

    def sweep(self) -> None:
        """One convergence pass, or a skip when the probe reports nothing changed and the
        floor isn't due. A segment that raises during derivation is stepped over rather
        than ending the tick, so one bad segment doesn't cost every later candidate its
        derivation and the drop pass behind it. ``candidacy()`` is this pass's one
        visibility evaluation; the drop pass below reuses its ``visible_segment_ids`` instead of re-evaluating it."""
        signature = self._events.derivation_signature()
        now = self._clock.now()
        floor_due = self._last_full_pass_at is None or now - self._last_full_pass_at >= _FORCED_FULL_PASS_FLOOR
        if not floor_due and signature == self._last_signature:
            _log.info("transcript event derivation sweep skipped", reason="signature unchanged")
            return

        read = self._service.candidacy()
        derived = 0
        failed = 0
        for segment_id in read.candidate_segment_ids:
            try:
                if self._service.derive_segment(segment_id):
                    derived += 1
            except Exception as exc:
                failed += 1
                _log.warning("segment derivation skipped", segment_id=segment_id, fault=repr(exc))

        stale = self._events.derived_segment_ids() - read.visible_segment_ids
        self._events.drop_segments(stale)

        self._last_signature = signature
        self._last_full_pass_at = now

        _log.info("transcript event derivation sweep completed", derived=derived, dropped=len(stale), failed=failed)
