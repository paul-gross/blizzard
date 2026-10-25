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

from structlog.contextvars import bound_contextvars

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.chunk.model import TransitionFact
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.kernel.tenant_fault import TenantFault
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


#: The signature of a tenant holding no segment and no chunk.
_GONE = DerivationSignature(segment_count=0, max_segment_id=None, max_received_at=None, chunk_count=0)
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

    def derivation_signature(self) -> Mapping[TenantId, DerivationSignature]:
        """The hub-wide change probe, one statement, its value per tenant over that tenant's segments
        and chunks; a tenant holding neither has no entry."""
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
    """The standing convergence pass, one per hub: derives each candidate, then drops rows for any
    segment no longer visible. Holds, in memory only, each tenant's last converged signature and
    instant, so a tenant is due on its own state alone (``bzh:pass-isolates-tenants``); a fresh process
    runs full, and :data:`FORCED_FULL_PASS_FLOOR` bounds staleness. ``tenant`` opens a tenant's half."""

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
        self._converged: dict[TenantId, tuple[DerivationSignature, datetime]] = {}
        self._swept = False
        # Tenants a pass left unconverged: one found only by the fresh-process derived read has no signature
        # or converged entry, so this alone keeps it known to the next tick.
        self._unconverged: set[TenantId] = set()

    def sweep(self) -> None:
        """One convergence pass over every due tenant (never converged, floor elapsed, or signature
        changed), or a skip when none is; the corpus is read only then. A segment is a
        ``(tenant, segment_id)`` pair — segment ids are runner-minted — and the stale set is the derived
        pairs less the visible ones. A closed tenant is skipped; one whose pins fail or whose half
        raises is left unconverged and retried alone next tick."""
        signatures = self._corpus.derivation_signature()
        now = self._clock.now()
        self._converged = {
            tenant: state for tenant, state in self._converged.items() if tenant in signatures or state[0] != _GONE
        }
        derived: Mapping[TenantId, frozenset[str]] | None = None
        known = set(signatures) | set(self._converged) | self._unconverged
        if not self._swept:  # a fresh process also finds tenants whose every segment and chunk is gone
            derived = self._corpus.derived_segment_ids()
            known |= set(derived)
        due = [tenant for tenant in sorted(known) if self._is_due(tenant, signatures.get(tenant, _GONE), now)]
        if not due:
            _log.info("transcript event derivation sweep skipped", reason="signature unchanged")
            return
        self._swept = True

        reads = self._corpus.candidacy(self._extractor_version)
        visible = {(tenant, segment) for tenant, read in reads.items() for segment in read.visible_segment_ids}
        if derived is None:
            derived = self._corpus.derived_segment_ids()
        derived_pairs = {(tenant, segment) for tenant, segments in derived.items() for segment in segments}
        stale: dict[TenantId, set[str]] = {}
        for tenant, segment in derived_pairs - visible:
            stale.setdefault(tenant, set()).add(segment)

        tally = DerivationTally()
        converged: list[TenantId] = []
        unconverged: list[TenantId] = []
        for tenant in due:
            with bound_contextvars(tenant_id=tenant):
                try:
                    ok = self._converge(tenant, reads.get(tenant), stale.get(tenant), tally)
                except (TenantClosed, UnknownTenant):
                    _log.info("transcript event derivation skipped a closed tenant")
                    self._unconverged.discard(tenant)
                    continue
                (converged if ok is True else unconverged).append(tenant)

        self._unconverged = (self._unconverged - set(due)) | set(unconverged)
        for tenant in converged:
            self._converged[tenant] = (signatures.get(tenant, _GONE), now)

        _log.info(
            "transcript event derivation sweep completed",
            derived=tally.derived,
            dropped=tally.dropped,
            failed=tally.failed,
            tenants_converged=len(converged),
            tenants_skipped=len(known) - len(due),
            tenants_unconverged=unconverged,
        )

    def sweep_tenant(self, tenant: TenantId) -> TenantFault | None:
        """:meth:`sweep` narrowed to ``tenant``: the same probe and corpus reads, ``tenant``'s share alone,
        and only ``tenant``'s entries of the convergence state touched; a tenant not due is skipped. Its
        fault is returned; a closed tenant's refusal propagates."""
        with bound_contextvars(tenant_id=tenant):
            signature = self._corpus.derivation_signature().get(tenant, _GONE)
            now = self._clock.now()
            if not self._is_due(tenant, signature, now):
                _log.info("transcript event derivation sweep skipped", reason="signature unchanged")
                return None
            read = self._corpus.candidacy(self._extractor_version).get(tenant)
            derived = self._corpus.derived_segment_ids().get(tenant, frozenset())
            stale = set(derived - (read.visible_segment_ids if read is not None else frozenset()))
            tally = DerivationTally()
            try:
                ok = self._converge(tenant, read, stale or None, tally)
            except (TenantClosed, UnknownTenant):
                self._unconverged.discard(tenant)
                raise
            if ok is True:
                self._unconverged.discard(tenant)
                self._converged[tenant] = (signature, now)
            else:
                self._unconverged.add(tenant)
            _log.info(
                "transcript event derivation sweep completed",
                derived=tally.derived,
                dropped=tally.dropped,
                failed=tally.failed,
                converged=ok is True,
            )
            return ok if isinstance(ok, TenantFault) else None

    def _converge(
        self, tenant: TenantId, read: CandidacyRead | None, stale: set[str] | None, tally: DerivationTally
    ) -> bool | TenantFault:
        """Converge ``tenant`` on its candidates in ``read`` and its ``stale`` segments; a tenant holding
        neither has converged. Its fault, or whether it converged; a closed tenant's refusal propagates."""
        if read is None and stale is None:
            return True
        candidates = (read or _NOTHING_VISIBLE).candidate_segment_ids
        try:
            return self._tenant(tenant).converge(candidates, frozenset(stale or ()), tally)
        except (TenantClosed, UnknownTenant):
            raise
        except Exception as exc:
            _log.warning("transcript event derivation failed for a tenant", fault=repr(exc))
            return TenantFault.of(exc)

    def _is_due(self, tenant: TenantId, signature: DerivationSignature, now: datetime) -> bool:
        last_signature, last_full_pass_at = self._converged.get(tenant, (None, None))
        return derivation_due(last_signature, signature, last_full_pass_at, now)
