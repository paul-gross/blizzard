"""Runner-reported fact intake — lease mints, escalations, and the rest of the runner's outbound facts.

:class:`FactIngestService` is the batched store-and-forward push — the one runner fact intake —
idempotent against a per-runner **high-water mark**, which each fact's own unit advances by compare-and-set.
Landing each lease mint is what keeps the epoch fence in lockstep across a chunk's successive node-steps. It
holds the **write** chunk seams each fact lands on (``bzh:controller-read-only``) and stamps landing time from
the injected clock."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.clock import IClock
from blizzard.foundation.event_log import EVENT_LOG_SEVERITY, EventLogKind, narrow_event_log_kind
from blizzard.foundation.fact_kinds import (
    ANSWER_DELIVERED,
    ESCALATION_RECORDED,
    EVENT_RECORDED,
    EXTERNAL_SUBSCRIPTION_USAGE_MISSED,
    EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED,
    LEASE_MINTED,
    QUESTION_ASKED,
    RUNNER_LOCALLY_PAUSED,
    RUNNER_LOCALLY_RESUMED,
    USAGE_RECORDED,
)
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.chunk.event_log import EventLogService, LoggedEvent
from blizzard.hub.domain.chunk.model import PROJECTED_EVENT_KINDS, Chunk, ChunkFacts, NodeQuestion, QuestionDelivery
from blizzard.hub.domain.chunk.ports.escalations import IWriteChunkEscalationsRepository
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.fence import Claimant, EpochAdmission, FenceRefusal
from blizzard.hub.domain.chunk.ports.questions import IWriteChunkQuestionsRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.chunk.ports.route import IWriteChunkRouteRepository
from blizzard.hub.domain.chunk.ports.usage import IWriteChunkUsageRepository
from blizzard.hub.domain.execution.auth.route import ROUTE_TOKEN_WARN, RouteToken
from blizzard.hub.domain.execution.fleet import FleetService
from blizzard.hub.domain.execution.payload import Payload
from blizzard.hub.domain.execution.usage_facts import (
    external_usage_windows_json,
    subscription_identity,
    usage_miss_reason,
)
from blizzard.hub.domain.kernel.high_water import HighWaterConflict
from blizzard.hub.domain.kernel.transactions import IHubTransactions, Transaction
from blizzard.hub.domain.runners.registration import RetiredRunnerGuard

_log = get_logger("blizzard.hub.facts")

# The chunk-scoped, fence-advancing kinds gated on intake: a fabricated one from a
# non-holder must not advance the fence or open a decision. Runner-scoped kinds are never gated.
_ROUTE_TOKEN_GATED_KINDS = frozenset({LEASE_MINTED, ESCALATION_RECORDED, QUESTION_ASKED})

# The kinds whose write takes its chunk's row lock: their unit declares that lock before any statement.
_CHUNK_LOCKED_KINDS = frozenset({LEASE_MINTED, ESCALATION_RECORDED, QUESTION_ASKED})


def _logged(written: int | LoggedEvent | None) -> LoggedEvent:
    """The event an ``event.recorded`` write answered."""
    if not isinstance(written, LoggedEvent):
        raise TypeError("the write answered no logged event")
    return written


def requires_route_token(kind: str, chunk_id: str | None) -> bool | None:
    """Whether a fact of ``kind`` is route-token gated. ``None`` refuses it outright: a gated
    fact that names no chunk has no route to authorize it against."""
    if kind not in _ROUTE_TOKEN_GATED_KINDS:
        return False
    return True if chunk_id is not None else None


def admitted_event_kind(kind: str, severity: str) -> EventLogKind | None:
    """The event-log kind a runner's ``event.recorded`` lands as — ``None`` for a kind outside
    the closed vocabulary, a kind the log only projects, or a severity other than that kind's own."""
    narrowed = narrow_event_log_kind(kind)
    if narrowed is None or narrowed in PROJECTED_EVENT_KINDS or severity != EVENT_LOG_SEVERITY[narrowed]:
        return None
    return narrowed


@domain_model
@dataclass(frozen=True)
class LocalPause:
    """A runner's local pause or resume, stamped when the runner decided — which may be an outage
    before its buffer drained — and attributed to the operator unless the fact names who."""

    paused: bool
    at: datetime
    by: str
    reason: str | None

    @classmethod
    def of(cls, kind: str, fact: Payload, *, now: datetime) -> LocalPause:
        return cls(
            paused=kind == RUNNER_LOCALLY_PAUSED,
            at=fact.instant("at", now),
            by=fact.string("by", "operator"),
            reason=fact.text("reason"),
        )


@domain_model
@dataclass(frozen=True)
class PushedFact:
    """One buffered runner fact: its per-runner ``seq``, its ``noun.verb`` kind, and its
    kind-specific payload."""

    seq: int
    kind: str
    payload: dict[str, object]


@domain_model
@dataclass(frozen=True)
class _Landing:
    """An admitted fact's write, decided from reads taken before its unit opens. ``write`` runs in the unit that
    advances the mark and answers the row id the ack carries — or the event it logged, whose row id the ack carries
    — or the write fence's refusal; ``locks`` names the chunk whose row lock that unit declares; ``committed`` runs
    on what ``write`` answered once the unit commits — a frame published, a line logged — and never when it rolls
    back (``bzh:publish-after-commit``)."""

    write: Callable[[Transaction], int | LoggedEvent | FenceRefusal | None]
    locks: str | None = None
    committed: Callable[[int | LoggedEvent | None], object] | None = None


@domain_model
@dataclass(frozen=True)
class FactIngestResult:
    """:meth:`FactIngestService.ingest`'s own return — the runner's new high-water mark, the
    pushed seqs partitioned into applied, already applied, and rejected for a non-idempotency
    reason, ``route_ended`` the subset of those refused because the fact's chunk has no live route,
    and per freshly-applied fact the id of the row it wrote. ``row_id_by_seq`` carries an
    entry only for a kind whose own id is not already in its payload."""

    high_water: int
    applied: list[int]
    already_applied: list[int]
    rejected: list[int]
    route_ended: list[int]
    row_id_by_seq: dict[int, int]


class FactIngestService:
    """Apply a runner's batched pushed facts idempotently against its high-water mark. Most facts are
    chunk-scoped and land through one of the seams above; ``fleet`` is here for the runner-scoped ones."""

    def __init__(
        self,
        *,
        facts: IReadChunkFactsRepository,
        chunks: IReadChunkRecordRepository,
        route: IWriteChunkRouteRepository,
        escalations: IWriteChunkEscalationsRepository,
        questions: IWriteChunkQuestionsRepository,
        usage: IWriteChunkUsageRepository,
        events: EventLogService,
        fleet: FleetService,
        retired: RetiredRunnerGuard,
        transactions: IHubTransactions,
        clock: IClock,
    ) -> None:
        self._facts = facts
        self._chunks = chunks
        self._route = route
        self._escalations = escalations
        self._questions = questions
        self._usage = usage
        self._events = events
        self._fleet = fleet
        self._retired = retired
        self._transactions = transactions
        self._clock = clock

    @property
    def usage(self) -> IWriteChunkUsageRepository:
        return self._usage

    def ingest(
        self,
        runner_id: str,
        pushed: Sequence[PushedFact],
        *,
        route_token_mode: str = ROUTE_TOKEN_WARN,
        runner_name: str | None = None,
    ) -> FactIngestResult:
        """Apply one push of ``runner_id``'s facts. A retired runner is refused with
        :class:`~blizzard.hub.domain.runners.registration.RunnerRetired` before its high-water mark is read, so
        nothing lands. ``runner_name`` is the pusher's registered name, carried onto the events the push records.

        Freshness is decided from the mark this push last read or set. Each fresh fact lands in its own unit with
        the mark's advance, which is conditioned on that seq (``bzh:write-reestablishes-read``): an overlapping push
        of the same runner's that moved the mark first rolls the fact back, and this push re-reads the mark and
        decides again from it, so no seq applies twice and the mark never moves back."""
        self._retired.refuse_if_retired(runner_id, action="fact ingest")
        mark = self._route.runner_high_water(runner_id)
        applied: list[int] = []
        already: list[int] = []
        rejected: list[int] = []
        route_ended: list[int] = []
        row_id_by_seq: dict[int, int] = {}

        chunks = self._event_chunks([fact for fact in pushed if fact.seq > mark])
        for fact in sorted(pushed, key=lambda f: f.seq):
            landed: tuple[bool, int | None] = (False, None)
            while fact.seq > mark:
                try:
                    landed = self._apply(
                        runner_id,
                        fact,
                        expected=mark,
                        route_token_mode=route_token_mode,
                        runner_name=runner_name,
                        chunks=chunks,
                    )
                    break
                except HighWaterConflict:
                    # Another push of this runner's moved the mark since this one read it, and the fact's unit
                    # rolled back with its row: decide again against the mark that push left. A mark that did not
                    # move names no such push, and deciding again would only refuse the same way.
                    moved = self._route.runner_high_water(runner_id)
                    if moved == mark:
                        raise
                    mark = moved
            if fact.seq <= mark:
                already.append(fact.seq)
                continue
            ok, row_id = landed
            if not ok:
                # A contract mismatch, not an idempotency skip: do not advance the mark
                # past it, and name it in the ack.
                rejected.append(fact.seq)
                if self._route_ended(fact):
                    route_ended.append(fact.seq)
                continue
            mark = fact.seq
            applied.append(fact.seq)
            if row_id is not None:
                row_id_by_seq[fact.seq] = row_id

        _log.info(
            "runner facts ingested",
            runner_id=runner_id,
            high_water=mark,
            applied=len(applied),
            already=len(already),
            rejected=len(rejected),
        )
        return FactIngestResult(
            high_water=mark,
            applied=applied,
            already_applied=already,
            rejected=rejected,
            route_ended=route_ended,
            row_id_by_seq=row_id_by_seq,
        )

    def _event_chunks(self, fresh: Sequence[PushedFact]) -> Mapping[str, Chunk]:
        """The chunk each of the push's fresh ``event.recorded`` facts names, in one batched read
        (``bzh:bulk-reconstitution``); a chunk the record no longer holds is absent."""
        chunk_ids = {
            chunk_id
            for fact in fresh
            if fact.kind == EVENT_RECORDED and (chunk_id := Payload(fact.payload).text("chunk_id")) is not None
        }
        if not chunk_ids:
            return {}
        return self._chunks.get_many(sorted(chunk_ids))

    def _route_ended(self, fact: PushedFact) -> bool:
        """Whether a rejected fact was refused because its chunk has no live route: a route-token-gated
        kind whose chunk's route is gone, whichever of the gate or the write fence refused it."""
        chunk_id = Payload(fact.payload).text("chunk_id")
        return (
            chunk_id is not None
            and requires_route_token(fact.kind, chunk_id) is True
            and self._route.route_of(chunk_id) is None
        )

    @staticmethod
    def _fenced(kind: str, fact: Payload, refusal: FenceRefusal) -> tuple[bool, None]:
        """A fact the write fence refused — rejected in the ack like a route-token refusal."""
        _log.warning("fact fenced", kind=kind, chunk_id=fact.text("chunk_id"), detail=refusal.detail)
        return False, None

    def _apply(
        self,
        runner_id: str,
        fact: PushedFact,
        *,
        expected: int,
        route_token_mode: str,
        chunks: Mapping[str, Chunk],
        runner_name: str | None = None,
    ) -> tuple[bool, int | None]:
        """Apply one fact in a unit that advances the mark from ``expected`` to its seq; ``(True, row_id)`` on
        success — ``row_id`` is the freshly-written row's own id only for a kind whose id is not already in its own
        payload (``escalation.recorded``/``event.recorded``), else ``None``. ``(False, None)`` on an unknown kind, a
        route-token rejection, or a write-fence refusal, which advance nothing. Raises :class:`HighWaterConflict`
        when the mark has moved from ``expected``, landing nothing."""
        now = self._clock.now()
        payload = Payload(fact.payload)
        landing = self._landing(
            runner_id,
            fact.kind,
            payload,
            route_token_mode=route_token_mode,
            chunks=chunks,
            runner_name=runner_name,
            now=now,
        )
        if landing is None:
            return False, None
        with self._transactions.begin() as unit:
            transaction = unit.lock_chunks([landing.locks]) if landing.locks is not None else unit
            written = landing.write(transaction)
            if isinstance(written, FenceRefusal):
                return self._fenced(fact.kind, payload, written)
            self._route.set_runner_high_water(
                runner_id, seq=fact.seq, expected=expected, at=now, transaction=transaction
            )
        if landing.committed is not None:
            landing.committed(written)
        return True, written.row_id if isinstance(written, LoggedEvent) else written

    def _landing(
        self,
        runner_id: str,
        kind: str,
        fact: Payload,
        *,
        route_token_mode: str,
        chunks: Mapping[str, Chunk],
        runner_name: str | None,
        now: datetime,
    ) -> _Landing | None:
        """The write that lands one fact, decided before its unit opens — the route-token gate and the answer
        delivery's guard read on their own connections — or ``None`` on an unknown kind or a refusal."""
        gated = requires_route_token(kind, fact.text("chunk_id"))
        if gated is None:
            return None
        if gated and not self._route_token_ok(fact.require_text("chunk_id"), runner_id, fact, mode=route_token_mode):
            return None
        if kind in _CHUNK_LOCKED_KINDS:
            return self._chunk_locked_landing(runner_id, kind, fact, now=now)
        if kind == USAGE_RECORDED:
            # No epoch fence and no route-token gate: trailing-epoch spend is real and attributed to its
            # own epoch (pinned in tests/component/hub/domain/execution/test_usage_facts_ingest.py,
            # tests/test_route_token_authz.py).
            return _Landing(
                write=lambda transaction: self._usage.record_usage(
                    fact.require_text("chunk_id"),
                    node_id=fact.require_text("node_id"),
                    epoch=fact.require_number("epoch"),
                    runner_id=runner_id,
                    kind=fact.require_text("kind"),
                    model=fact.require_text("model"),
                    harness_id=fact.text("harness_id"),
                    harness_version=fact.text("harness_version"),
                    input_tokens=fact.require_number("input_tokens"),
                    output_tokens=fact.require_number("output_tokens"),
                    cache_read_tokens=fact.require_number("cache_read_tokens"),
                    cache_create_tokens=fact.require_number("cache_create_tokens"),
                    cost_usd=fact.amount("cost_usd"),
                    estimated_cost_usd=fact.amount("estimated_cost_usd"),
                    at=now,
                    transaction=transaction,
                )
            )
        if kind == EVENT_RECORDED:
            return self._event_landing(runner_id, fact, chunks=chunks, runner_name=runner_name, now=now)
        if kind == ANSWER_DELIVERED:
            # Records that the resume-with-answer ran; derives no status of its own.
            return self._answer_landing(fact.require_text("question_id"), fact.require_text("chunk_id"), now=now)
        if kind in (EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED, EXTERNAL_SUBSCRIPTION_USAGE_MISSED):
            return self._external_usage_landing(runner_id, kind, fact, now=now)
        if kind in (RUNNER_LOCALLY_PAUSED, RUNNER_LOCALLY_RESUMED):
            # Runner-scoped and hub-read-only; stamped as :class:`LocalPause` states.
            pause = LocalPause.of(kind, fact, now=now)
            return _Landing(
                write=lambda transaction: self._fleet.record_local_pause(
                    runner_id,
                    paused=pause.paused,
                    at=pause.at,
                    by=pause.by,
                    reason=pause.reason,
                    transaction=transaction,
                ),
                committed=lambda _: _log.info(
                    "runner local pause reported",
                    runner_id=runner_id,
                    paused=pause.paused,
                    by=pause.by,
                    reason=pause.reason,
                ),
            )
        _log.warning("unknown runner fact kind", kind=kind)
        return None

    def _chunk_locked_landing(self, runner_id: str, kind: str, fact: Payload, *, now: datetime) -> _Landing:
        """A fence-advancing fact's write, under its chunk's row lock, which its unit declares."""
        chunk_id = fact.require_text("chunk_id")
        claimant = Claimant(runner_id, fact.text("lease_id"))
        if kind == LEASE_MINTED:
            return _Landing(
                locks=chunk_id,
                write=lambda transaction: self._route.record_lease_minted(
                    chunk_id, epoch=fact.require_number("epoch"), claimant=claimant, at=now, transaction=transaction
                ),
            )
        if kind == ESCALATION_RECORDED:
            return _Landing(
                locks=chunk_id,
                write=lambda transaction: self._escalations.record_escalation(
                    chunk_id,
                    epoch=fact.require_number("epoch"),
                    admission=EpochAdmission.AT_OR_ABOVE,
                    claimant=claimant,
                    takeover_command=fact.string("takeover_command"),
                    wrapped_takeover_command=fact.string("wrapped_takeover_command"),
                    cause=fact.text("cause"),
                    detail=fact.text("detail"),
                    at=now,
                    transaction=transaction,
                ),
            )
        # question.asked — the runner authors the question_id so it can poll the answer back.
        return _Landing(
            locks=chunk_id,
            write=lambda transaction: self._questions.record_question(
                question_id=fact.require_text("question_id"),
                chunk_id=chunk_id,
                node_id=fact.text("node_id"),
                session_id=fact.text("session_id"),
                harness_id=fact.text("harness_id"),
                runner_id=runner_id,
                epoch=fact.require_number("epoch"),
                admission=EpochAdmission.AT_OR_ABOVE,
                claimant=claimant,
                question=fact.require_text("question"),
                options=fact.strings("options"),
                asked_at=fact.instant("asked_at", now),
                transaction=transaction,
            ),
        )

    def _event_landing(
        self, runner_id: str, fact: Payload, *, chunks: Mapping[str, Chunk], runner_name: str | None, now: datetime
    ) -> _Landing | None:
        """An ``event.recorded``'s log row, whose ``event-logged`` frame publishes once its unit commits. Neither
        epoch-fenced nor route-token-gated: an event from a fenced-out or dying worker is exactly the signal this
        log exists to surface. ``chunk_id`` is optional."""
        wire_kind = admitted_event_kind(fact.require_text("kind"), fact.require_text("severity"))
        if wire_kind is None:
            _log.warning("event fact not admitted to the log", kind=fact.require_text("kind"))
            return None
        chunk_id = fact.text("chunk_id")
        chunk = chunks.get(chunk_id or "")
        project_id = chunk.project_id if chunk is not None else None
        return _Landing(
            write=lambda transaction: self._events.record(
                kind=wire_kind,
                runner_id=runner_id,
                runner_name=runner_name,
                chunk_id=chunk_id,
                project_id=project_id,
                lease_id=fact.text("lease_id"),
                node_name=fact.text("node_name"),
                message=fact.string("message"),
                detail=fact.mapping("detail"),
                at=now,
                transaction=transaction,
            ),
            committed=lambda written: self._events.publish(_logged(written)),
        )

    def _external_usage_landing(self, runner_id: str, kind: str, fact: Payload, *, now: datetime) -> _Landing | None:
        """A subscription's sample or miss — advisory, each refreshed in place per ``(runner_id, slug)`` in its own
        table, a miss never touching the sample row."""
        identity = subscription_identity(fact)
        if identity is None:
            return None
        slug, name = identity
        if kind == EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED:
            windows_json = external_usage_windows_json(fact.get("windows", []), owner_id=runner_id, slug=slug)
            sampled_at = fact.instant("sampled_at", now)
            return _Landing(
                write=lambda transaction: self._fleet.record_external_usage(
                    runner_id,
                    slug=slug,
                    name=name,
                    sampled_at=sampled_at,
                    windows_json=windows_json,
                    at=now,
                    transaction=transaction,
                ),
                committed=lambda _: _log.info(
                    "runner external usage sample landed", runner_id=runner_id, slug=slug, sampled_at=sampled_at
                ),
            )
        reason = usage_miss_reason(fact)
        if reason is None:
            return None
        missed_at = fact.instant("missed_at", now)
        return _Landing(
            write=lambda transaction: self._fleet.record_external_usage_miss(
                runner_id,
                slug=slug,
                name=name,
                missed_at=missed_at,
                reason=reason,
                at=now,
                transaction=transaction,
            ),
            committed=lambda _: _log.info(
                "runner external usage miss landed", runner_id=runner_id, slug=slug, reason=reason, missed_at=missed_at
            ),
        )

    def _answer_landing(self, question_id: str, chunk_id: str, *, now: datetime) -> _Landing | None:
        """An ``answer.delivered`` as :meth:`NodeQuestion.delivery` decides: written, a replay that writes nothing
        but still advances the mark, or ``None`` — rejected in the ack (an unknown question too)."""
        question: NodeQuestion | None = self._questions.get_question(question_id)
        if question is None:
            _log.warning("answer delivery for an unknown question", question_id=question_id)
            return None
        facts = ChunkFacts.or_default(self._facts.load_facts(question.chunk_id))
        delivery, detail = question.delivery(
            chunk_id=chunk_id, superseded_by_restart=facts.restarted_past(question.epoch)
        )
        if delivery is QuestionDelivery.REFUSE:
            _log.warning("answer delivery rejected", question_id=question_id, detail=detail)
            return None
        if delivery is QuestionDelivery.REPLAY:
            return _Landing(write=lambda transaction: None)
        return _Landing(
            write=lambda transaction: self._questions.record_answer_delivered(
                question_id=question_id, chunk_id=chunk_id, at=now, transaction=transaction
            )
        )

    def _route_token_ok(self, chunk_id: str, runner_id: str, fact: Payload, *, mode: str) -> bool:
        """Route-token authorization for a chunk-scoped, fence-advancing fact — the
        buffered-push counterpart of ``src/blizzard/hub/domain/execution/apply.py``'s own check. A chunk the
        hub has never minted (``load_facts`` returns ``None``, e.g. a malformed/stale
        payload) falls back to an empty :class:`ChunkFacts`, which
        :class:`RouteToken` already rejects as having no live route."""
        facts = ChunkFacts.or_default(self._facts.load_facts(chunk_id))
        route = self._route.route_of(chunk_id)
        detail = RouteToken(
            facts=facts,
            presented=fact.text("route_token"),
            submission_runner_id=runner_id,
            route_runner_id=route.runner_id if route is not None else None,
        ).rejection(mode=mode)
        if detail is not None:
            _log.warning(
                "route token check rejected buffered fact", chunk_id=chunk_id, runner_id=runner_id, detail=detail
            )
            return False
        return True
