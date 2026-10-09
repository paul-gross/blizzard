"""The per-segment replacement unit and the standing convergence sweep.

There is no finalize hook to derive from: the sweep is the only first-derivation
path, and re-running it is the re-derive path — one engine, one convergence property
(``bzh:domain-core``, ``bzh:steppable-loop``)."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

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
from blizzard.hub.tenancy import TenantClosed, TenantId, UnknownTenant

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
    """What one re-derive covers: one segment, forced regardless of its candidacy but only while visible; or the
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
    """Segments a re-derive derived (successes only), candidates remaining, and whether a segment force was refused."""

    derived: int
    remaining: int
    not_visible: bool = False


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


#: A tenant's candidacy when it has no visible segment.
_NOTHING_VISIBLE = CandidacyRead(visible_segment_ids=frozenset(), candidate_segment_ids=[])

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


class IReadDerivationCorpus(Protocol):
    """The event-derivation sweep's corpus, read across every open tenant, and its probe."""

    def derivation_signature(self) -> DerivationSignature:
        """The hub-wide change probe over every tenant's segments and chunks."""
        ...

    def candidacy(self, extractor_version: str) -> Mapping[TenantId, CandidacyRead]:
        """Each open tenant's visible segments and its candidates for ``extractor_version``, keyed
        by tenant; a tenant with no visible segment has no entry."""
        ...

    def derived_segment_ids(self) -> Mapping[TenantId, frozenset[str]]:
        """Each open tenant's segments carrying any derivation marker, keyed by tenant."""
        ...


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

    @property
    def extractor_version(self) -> str:
        """The extractor version this service derives and marks segments at."""
        return self._extractor_version

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
        with no resolvable graph pin, is not counted. A segment that is not visible is refused: no derive, no marker."""
        if scope.segment_id is not None:
            if not self._events.visible_segment_ids(segment_id=scope.segment_id):
                return ReDeriveOutcome(derived=0, remaining=0, not_visible=True)
            pins = self.graph_pins_for([scope.segment_id])
            return ReDeriveOutcome(derived=1 if self.derive_segment(scope.segment_id, pins) else 0, remaining=0)
        to_derive, remaining = scope.batch(self.candidate_segment_ids(chunk_id=scope.chunk_id), limit)
        pins = self.graph_pins_for(to_derive)
        derived = sum(1 for segment_id in to_derive if self.derive_segment(segment_id, pins))
        return ReDeriveOutcome(derived=derived, remaining=remaining)


@domain_model
@dataclass
class DerivationTally:
    """One derivation pass's counts, across every tenant it acted in."""

    derived: int = 0
    dropped: int = 0
    failed: int = 0


class TenantEventDerivation:
    """One tenant's half of the derivation pass: derives the candidates a pass hands it and drops
    its segments no longer visible, through ``service`` and ``events`` — both that one tenant's."""

    def __init__(self, *, service: EventDerivationService, events: IWriteTranscriptEvents, clock: IClock) -> None:
        self._service = service
        self._events = events
        self._clock = clock

    def converge(self, candidates: Sequence[str], stale: frozenset[str], tally: DerivationTally) -> bool:
        """Derive each of ``candidates`` and drop ``stale``'s rows, counting into ``tally``. Graph-pin
        resolution failing skips this tenant's share of the pass and answers ``False``, so the pass
        is retried next tick; a single segment raising during derivation only costs that segment."""
        try:
            pins = self._service.graph_pins_for(candidates)
        except Exception as exc:
            _log.warning("graph pin resolution failed, tenant skipped", fault=repr(exc))
            return False
        for segment_id in candidates:
            try:
                if self._service.derive_segment(segment_id, pins):
                    tally.derived += 1
            except Exception as exc:
                tally.failed += 1
                _log.warning("segment derivation skipped", segment_id=segment_id, fault=repr(exc))
        self._events.drop_segments(stale, at=self._clock.now())
        tally.dropped += len(stale)
        return True


class EventDerivationReconciler:
    """The standing convergence pass, one per hub, stepped by the existing ``Sweep`` driver: derives
    each candidate, then drops rows for any segment no longer visible. Holds, in memory only, the
    last pass's :class:`~blizzard.hub.domain.observability.analytics.events.DerivationSignature`; a
    fresh process always runs full, and :data:`FORCED_FULL_PASS_FLOOR` bounds staleness. ``corpus``
    names every open tenant's segments, each beside its tenant; ``tenant`` opens a row's tenant's
    :class:`TenantEventDerivation`, which acts on that tenant's segments alone."""

    def __init__(
        self,
        *,
        corpus: IReadDerivationCorpus,
        tenant: Callable[[TenantId], TenantEventDerivation],
        clock: IClock,
        extractor_version: str = EXTRACTOR_VERSION,
    ) -> None:
        self._corpus = corpus
        self._tenant = tenant
        self._clock = clock
        self._extractor_version = extractor_version
        self._last_signature: DerivationSignature | None = None
        self._last_full_pass_at: datetime | None = None

    def sweep(self) -> None:
        """One convergence pass, or a skip when the hub-wide probe reports nothing changed and the
        floor isn't due. A segment is a ``(tenant, segment_id)`` pair throughout — segment ids are
        runner-minted, so two tenants may each hold one id — and the stale set is the derived pairs
        less the visible ones. A tenant closed since the corpus read is skipped; a tenant whose
        graph pins fail to resolve leaves the pass unconverged, retried next tick."""
        signature = self._corpus.derivation_signature()
        now = self._clock.now()
        if not derivation_due(self._last_signature, signature, self._last_full_pass_at, now):
            _log.info("transcript event derivation sweep skipped", reason="signature unchanged")
            return

        reads = self._corpus.candidacy(self._extractor_version)
        visible = {(tenant, segment) for tenant, read in reads.items() for segment in read.visible_segment_ids}
        derived_pairs = {
            (tenant, segment) for tenant, segments in self._corpus.derived_segment_ids().items() for segment in segments
        }
        stale: dict[TenantId, set[str]] = {}
        for tenant, segment in derived_pairs - visible:
            stale.setdefault(tenant, set()).add(segment)

        tally = DerivationTally()
        converged = True
        for tenant in sorted(set(reads) | set(stale)):
            candidates = reads.get(tenant, _NOTHING_VISIBLE).candidate_segment_ids
            try:
                converged &= self._tenant(tenant).converge(candidates, frozenset(stale.get(tenant, ())), tally)
            except (TenantClosed, UnknownTenant):
                _log.info("transcript event derivation skipped a closed tenant", tenant=tenant)

        if converged:
            self._last_signature = signature
            self._last_full_pass_at = now

        _log.info(
            "transcript event derivation sweep completed",
            derived=tally.derived,
            dropped=tally.dropped,
            failed=tally.failed,
        )
