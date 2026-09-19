"""The per-segment replacement unit and the standing convergence sweep (blizzard#254).

There is no finalize hook to derive from (D1/D2): the sweep is the only first-derivation
path, and re-running it is the re-derive path — one engine, one convergence property
(``bzh:domain-core``, ``bzh:steppable-loop``)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
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
from blizzard.hub.domain.work import TransitionFact

_log = get_logger("blizzard.hub.transcript_events")


@dataclass(frozen=True)
class GraphPins:
    """Per chunk, its transitions and mint pin (D4), pre-resolved once for a whole
    pass. Owns the "newest matching transition, else mint pin" rule
    :meth:`EventDerivationService.derive_segment` applies per node-step."""

    transitions_by_chunk: dict[str, list[TransitionFact]] = field(default_factory=dict)
    mint_pin_by_chunk: dict[str, str] = field(default_factory=dict)

    def graph_id_for(self, chunk_id: str, node_id: str, epoch: int) -> str | None:
        """The node-step's graph (D4): the newest transition among this chunk's
        pre-resolved ones matching ``node_id``/``epoch``, else the chunk's mint pin, else
        ``None`` when neither resolves (an unresolvable chunk)."""
        matches = [
            t
            for t in self.transitions_by_chunk.get(chunk_id, [])
            if t.to_node_id == node_id and t.epoch == epoch and t.graph_id is not None
        ]
        if matches:
            newest = max(matches, key=lambda t: t.recorded_at)
            assert newest.graph_id is not None  # narrowed by the filter above
            return newest.graph_id
        return self.mint_pin_by_chunk.get(chunk_id)


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
        """:meth:`IReadTranscriptEvents.candidacy`, fixed to this service's own
        ``extractor_version``. ``chunk_id`` narrows the read to one chunk when given
        (D7); omitted, the whole visible set is evaluated."""
        return self._events.candidacy(self._extractor_version, chunk_id=chunk_id)

    def candidate_segment_ids(self, *, chunk_id: str | None = None) -> list[str]:
        """Every visible segment (D1) lacking a current-version marker, or whose marker
        disagrees with the segment's stored content today (D2), read via
        :meth:`candidacy`."""
        return self.candidacy(chunk_id=chunk_id).candidate_segment_ids

    def graph_pins_for(self, segment_ids: Sequence[str]) -> GraphPins:
        """Every one of ``segment_ids``' resolved chunks' graph pins, in two bulk reads
        for the whole batch (D4): one ``segment_contexts`` call resolves the chunk ids,
        then one ``load_facts_for`` plus one ``graph_id_of_many`` call resolves the
        distinct chunk set's transitions and mint pins. Empty input issues neither read."""
        if not segment_ids:
            return GraphPins()
        chunk_ids = sorted({context.chunk_id for context in self._events.segment_contexts(segment_ids).values()})
        if not chunk_ids:
            return GraphPins()
        facts_by_id = self._facts.load_facts_for(chunk_ids)
        return GraphPins(
            transitions_by_chunk={chunk_id: facts.transitions for chunk_id, facts in facts_by_id.items()},
            mint_pin_by_chunk=self._record.graph_id_of_many(chunk_ids),
        )

    def derive_segment(self, segment_id: str, pins: GraphPins) -> bool:
        """One transaction: recognize every event this segment's turns hold today, stamp
        the node-step context, and replace this ``(segment_id, extractor_version)`` pair's
        rows and marker (D6). Returns ``False``, deriving nothing, for a segment gone by
        now or one whose chunk has no resolvable graph pin. ``pins`` is the caller's own
        already-resolved :class:`GraphPins`, built once per pass by :meth:`graph_pins_for`."""
        current = self._events.segment_derivation_input(segment_id)
        if current is None:
            return False
        graph_id = pins.graph_id_for(current.chunk_id, current.node_id, current.epoch)
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
            provenance=current.provenance,
        )
        return True


#: The change probe's forced floor (blizzard#524 D5) — bounds how stale a missed signature can leave reality.
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
        floor isn't due. Graph-pin resolution failing skips the whole pass, logged and
        retried next tick; a single segment raising during derivation only costs that
        segment. ``candidacy()``'s ``visible_segment_ids`` is reused by the drop pass
        below, not re-evaluated."""
        signature = self._events.derivation_signature()
        now = self._clock.now()
        floor_due = self._last_full_pass_at is None or now - self._last_full_pass_at >= _FORCED_FULL_PASS_FLOOR
        if not floor_due and signature == self._last_signature:
            _log.info("transcript event derivation sweep skipped", reason="signature unchanged")
            return

        read = self._service.candidacy()
        try:
            pins = self._service.graph_pins_for(read.candidate_segment_ids)
        except Exception as exc:
            _log.warning("graph pin resolution failed, sweep skipped", fault=repr(exc))
            return
        derived = 0
        failed = 0
        for segment_id in read.candidate_segment_ids:
            try:
                if self._service.derive_segment(segment_id, pins):
                    derived += 1
            except Exception as exc:
                failed += 1
                _log.warning("segment derivation skipped", segment_id=segment_id, fault=repr(exc))

        stale = self._events.derived_segment_ids() - read.visible_segment_ids
        self._events.drop_segments(stale)

        self._last_signature = signature
        self._last_full_pass_at = now

        _log.info("transcript event derivation sweep completed", derived=derived, dropped=len(stale), failed=failed)
