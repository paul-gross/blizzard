"""The per-segment replacement unit and the standing convergence sweep.

There is no finalize hook to derive from: the sweep is the only first-derivation
path, and re-running it is the re-derive path — one engine, one convergence property
(``bzh:domain-core``, ``bzh:steppable-loop``)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.chunk.model import TransitionFact
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.observability.analytics.events import (
    CandidacyRead,
    DerivationSignature,
    IWriteTranscriptEvents,
    SegmentDerivationInput,
    TranscriptEvent,
)
from blizzard.hub.domain.observability.analytics.extraction import (
    DEFAULT_EXTRACTORS,
    EXTRACTOR_VERSION,
    ExtractedEvent,
    ITurnEventExtractor,
    extract_events,
)

_log = get_logger("blizzard.hub.transcript_events")


@domain_model
@dataclass(frozen=True)
class GraphPins:
    """Per chunk, its transitions and mint pin, pre-resolved once for a whole
    pass. Owns the "newest matching transition, else mint pin" rule
    :meth:`EventDerivationService.derive_segment` applies per node-step."""

    transitions_by_chunk: dict[str, list[TransitionFact]] = field(default_factory=dict)
    mint_pin_by_chunk: dict[str, str] = field(default_factory=dict)

    def graph_id_for(self, chunk_id: str, node_id: str, epoch: int) -> str | None:
        """The node-step's graph: the newest transition among this chunk's
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


class ReDeriveScopeRefused(ValueError):
    """A re-derive naming both a segment and a chunk."""


@domain_model
@dataclass(frozen=True)
class ReDeriveScope:
    """What one re-derive covers: one segment, forced regardless of its candidacy; or the
    candidates of one chunk, or of every chunk. An id that names nothing derives nothing —
    re-derive is a convergence trigger, not a read."""

    segment_id: str | None = None
    chunk_id: str | None = None

    @classmethod
    def of(cls, *, segment_id: str | None, chunk_id: str | None) -> ReDeriveScope:
        if segment_id is not None and chunk_id is not None:
            raise ReDeriveScopeRefused("segment_id and chunk_id are mutually exclusive")
        return cls(segment_id, chunk_id)

    def batch(self, candidates: Sequence[str], limit: int) -> tuple[list[str], int]:
        """The candidates one call derives — the first ``limit`` — and how many remain for the
        caller's next call."""
        to_derive = list(candidates[:limit])
        return to_derive, len(candidates) - len(to_derive)


@domain_model
@dataclass(frozen=True)
class ReDeriveOutcome:
    """How many segments a re-derive derived — successes only — and how many candidates remain."""

    derived: int
    remaining: int


def stamp_events(
    extracted: Sequence[ExtractedEvent], current: SegmentDerivationInput, graph_id: str
) -> list[TranscriptEvent]:
    """Each extracted event stamped with its segment's node-step context and graph."""
    return [
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


#: The change probe's forced floor — bounds how stale a missed signature can leave reality.
FORCED_FULL_PASS_FLOOR = timedelta(minutes=10)


def derivation_due(
    last_signature: DerivationSignature | None,
    signature: DerivationSignature,
    last_full_pass_at: datetime | None,
    now: datetime,
) -> bool:
    """A pass runs when the inputs' signature changed since the last pass, or the forced floor
    has elapsed since it — always for a process that has run none."""
    if last_full_pass_at is None or now - last_full_pass_at >= FORCED_FULL_PASS_FLOOR:
        return True
    return signature != last_signature


class EventDerivationService:
    """The per-segment replacement unit and the candidate-set predicate.

    ``facts``/``record`` resolve a node-step's ``graph_id``: the latest matching
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
        ``extractor_version``. ``chunk_id`` narrows the read to one chunk when given;
        omitted, the whole visible set is evaluated."""
        return self._events.candidacy(self._extractor_version, chunk_id=chunk_id)

    def candidate_segment_ids(self, *, chunk_id: str | None = None) -> list[str]:
        """Every visible segment lacking a current-version marker, or whose marker
        disagrees with the segment's stored content today, read via
        :meth:`candidacy`."""
        return self.candidacy(chunk_id=chunk_id).candidate_segment_ids

    def graph_pins_for(self, segment_ids: Sequence[str]) -> GraphPins:
        """Every one of ``segment_ids``' resolved chunks' graph pins, in two bulk reads
        for the whole batch: one ``segment_contexts`` call resolves the chunk ids,
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
        rows and marker. Returns ``False``, deriving nothing, for a segment gone by
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
        events = stamp_events(extracted, current, graph_id)
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

    def re_derive(self, scope: ReDeriveScope, *, limit: int) -> ReDeriveOutcome:
        """Derive ``scope``: its one segment, or up to ``limit`` of its current candidates.
        ``derived`` counts the segments actually derived — a candidate gone by now, or one
        with no resolvable graph pin, is not counted."""
        if scope.segment_id is not None:
            pins = self.graph_pins_for([scope.segment_id])
            return ReDeriveOutcome(derived=1 if self.derive_segment(scope.segment_id, pins) else 0, remaining=0)
        to_derive, remaining = scope.batch(self.candidate_segment_ids(chunk_id=scope.chunk_id), limit)
        pins = self.graph_pins_for(to_derive)
        derived = sum(1 for segment_id in to_derive if self.derive_segment(segment_id, pins))
        return ReDeriveOutcome(derived=derived, remaining=remaining)


class EventDerivationReconciler:
    """The standing convergence pass, stepped by the existing ``Sweep`` driver: derives
    each candidate, then drops rows for any segment no longer visible. Holds, in memory
    only, the last pass's :class:`~blizzard.hub.domain.observability.analytics.events.DerivationSignature`;
    a fresh process always runs full, and :data:`FORCED_FULL_PASS_FLOOR` bounds staleness."""

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
        if not derivation_due(self._last_signature, signature, self._last_full_pass_at, now):
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
        self._events.drop_segments(stale, at=self._clock.now())

        self._last_signature = signature
        self._last_full_pass_at = now

        _log.info("transcript event derivation sweep completed", derived=derived, dropped=len(stale), failed=failed)
