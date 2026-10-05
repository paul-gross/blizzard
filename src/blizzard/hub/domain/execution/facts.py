"""Runner-reported fact intake — lease mints, escalations, and the rest of the runner's outbound facts.

:class:`FactIngestService` is the batched store-and-forward push — the one runner fact intake —
idempotent against a per-runner **high-water mark**. Landing each lease mint is what keeps the epoch
fence in lockstep across a chunk's successive node-steps. It holds the **write** chunk seams each fact
lands on (``bzh:controller-read-only``) and stamps landing time from the injected clock."""

from __future__ import annotations

import json
from collections.abc import Sequence
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
from blizzard.foundation.subscription_miss import SampleMissReason
from blizzard.hub.config import ROUTE_TOKEN_WARN
from blizzard.hub.domain.chunk.event_log import EventLogService
from blizzard.hub.domain.chunk.model import ChunkFacts, NodeQuestion, QuestionDelivery
from blizzard.hub.domain.chunk.ports.escalations import IWriteChunkEscalationsRepository
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.fence import Claimant, EpochAdmission, FenceRefusal
from blizzard.hub.domain.chunk.ports.questions import IWriteChunkQuestionsRepository
from blizzard.hub.domain.chunk.ports.route import IWriteChunkRouteRepository
from blizzard.hub.domain.chunk.ports.usage import IWriteChunkUsageRepository
from blizzard.hub.domain.execution.auth.route import RouteToken
from blizzard.hub.domain.execution.fleet import FleetService
from blizzard.hub.domain.execution.questions import parse_instant
from blizzard.hub.domain.runners.registration import ExternalSubscriptionUsageWindow, RetiredRunnerGuard

_log = get_logger("blizzard.hub.facts")

# The chunk-scoped, fence-advancing kinds gated on intake: a fabricated one from a
# non-holder must not advance the fence or open a decision. Runner-scoped kinds are never gated.
_ROUTE_TOKEN_GATED_KINDS = frozenset({LEASE_MINTED, ESCALATION_RECORDED, QUESTION_ASKED})


def _external_usage_windows_json(raw: object, *, runner_id: str, slug: str) -> str:
    """The complete usage windows from a fact; malformed entries are omitted at intake.

    Instants are normalized to UTC here, so a naive or offset ``resets_at`` is stored
    already-UTC rather than repaired on every later read (``bzh:utc-instants``)."""
    if not isinstance(raw, list):
        _log.warning("external usage windows not a list", runner_id=runner_id, slug=slug)
        return "[]"
    windows = []
    for entry in raw:
        window = ExternalSubscriptionUsageWindow.admitted(entry)
        if isinstance(window, str):
            _log.warning("dropped malformed external usage window", runner_id=runner_id, slug=slug, reason=window)
            continue
        windows.append(window.stored)
    return json.dumps(windows)


@domain_model
@dataclass(frozen=True)
class Payload:
    """One pushed fact's body, read through the coercions the intake shares.

    An absent key and an explicit ``null`` are distinct: :meth:`text` reads both as
    ``None``, while :meth:`string`'s default only covers the absent one."""

    body: dict[str, object]

    def get(self, key: str, default: object = None) -> object:
        return self.body.get(key, default)

    def text(self, key: str) -> str | None:
        value = self.body.get(key)
        return str(value) if value is not None else None

    def string(self, key: str, default: str = "") -> str:
        return str(self.body.get(key, default))

    def require_text(self, key: str) -> str:
        return str(self.body[key])

    def require_number(self, key: str) -> int:
        return int(self.body[key])  # type: ignore[arg-type]

    def amount(self, key: str) -> float | None:
        """A usage fact's dollar figure by ``key`` — ``cost_usd`` or ``estimated_cost_usd`` — ``None``
        stays ``None`` (no such figure was reported), never fabricated."""
        value = self.body.get(key)
        return float(value) if value is not None else None  # type: ignore[arg-type]

    def strings(self, key: str) -> list[str]:
        return [str(item) for item in self.body.get(key, [])]  # type: ignore[union-attr]

    def mapping(self, key: str) -> dict[str, object] | None:
        value = self.body.get(key)
        return value if isinstance(value, dict) else None

    def instant(self, key: str, fallback: datetime) -> datetime:
        """An ISO-8601 stamp, falling back on a malformed one and coerced to UTC
        (``bzh:utc-instants``) — store-and-forward resends the naive stamp it buffered."""
        return parse_instant(self.body.get(key), fallback)


def requires_route_token(kind: str, chunk_id: str | None) -> bool | None:
    """Whether a fact of ``kind`` is route-token gated. ``None`` refuses it outright: a gated
    fact that names no chunk has no route to authorize it against."""
    if kind not in _ROUTE_TOKEN_GATED_KINDS:
        return False
    return True if chunk_id is not None else None


def admitted_event_kind(kind: str, severity: str) -> EventLogKind | None:
    """The event-log kind a runner's ``event.recorded`` lands as — ``None`` for a kind outside
    the closed vocabulary or a severity other than that kind's own."""
    narrowed = narrow_event_log_kind(kind)
    if narrowed is None or severity != EVENT_LOG_SEVERITY[narrowed]:
        return None
    return narrowed


def subscription_identity(fact: Payload) -> tuple[str, str] | None:
    """An external-subscription usage fact's ``(slug, name)`` — ``None`` without a non-empty
    string slug; the name defaults to the slug."""
    slug = fact.get("slug")
    if not isinstance(slug, str) or not slug:
        return None
    return slug, fact.text("name") or slug


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
class FactIngestResult:
    """:meth:`FactIngestService.ingest`'s own return — the runner's new high-water mark, the
    pushed seqs partitioned into applied, already applied, and rejected for a non-idempotency
    reason, and per freshly-applied fact the id of the row it wrote. ``row_id_by_seq`` carries an
    entry only for a kind whose own id is not already in its payload."""

    high_water: int
    applied: list[int]
    already_applied: list[int]
    rejected: list[int]
    row_id_by_seq: dict[int, int]


class FactIngestService:
    """Apply a runner's batched pushed facts idempotently against its high-water mark. Most facts are
    chunk-scoped and land through one of the seams above; ``fleet`` is here for the runner-scoped ones."""

    def __init__(
        self,
        *,
        facts: IReadChunkFactsRepository,
        route: IWriteChunkRouteRepository,
        escalations: IWriteChunkEscalationsRepository,
        questions: IWriteChunkQuestionsRepository,
        usage: IWriteChunkUsageRepository,
        events: EventLogService,
        fleet: FleetService,
        retired: RetiredRunnerGuard,
        clock: IClock,
    ) -> None:
        self._facts = facts
        self._route = route
        self._escalations = escalations
        self._questions = questions
        self._usage = usage
        self._events = events
        self._fleet = fleet
        self._retired = retired
        self._clock = clock

    def ingest(
        self, runner_id: str, pushed: Sequence[PushedFact], *, route_token_mode: str = ROUTE_TOKEN_WARN
    ) -> FactIngestResult:
        """Apply one push of ``runner_id``'s facts. A retired runner is refused with
        :class:`RunnerRetired` before its high-water mark is read, so nothing in the push lands."""
        self._retired.refuse_if_retired(runner_id, action="fact ingest")
        mark = self._route.runner_high_water(runner_id)
        applied: list[int] = []
        already: list[int] = []
        rejected: list[int] = []
        row_id_by_seq: dict[int, int] = {}

        for fact in sorted(pushed, key=lambda f: f.seq):
            if fact.seq <= mark:
                already.append(fact.seq)
                continue
            ok, row_id = self._apply(runner_id, fact.kind, fact.payload, route_token_mode=route_token_mode)
            if not ok:
                # A contract mismatch, not an idempotency skip: do not advance the mark
                # past it, and name it in the ack.
                rejected.append(fact.seq)
                continue
            mark = fact.seq
            applied.append(fact.seq)
            if row_id is not None:
                row_id_by_seq[fact.seq] = row_id
            # Persisted per applied fact, not once after the loop — bounds a crash mid-batch
            # to the one in-flight fact's double-apply (blizzard-context crash-correctness/hub.md).
            self._route.set_runner_high_water(runner_id, seq=mark, at=self._clock.now())

        _log.info(
            "runner facts ingested",
            runner_id=runner_id,
            high_water=mark,
            applied=len(applied),
            already=len(already),
            rejected=len(rejected),
        )
        return FactIngestResult(
            high_water=mark, applied=applied, already_applied=already, rejected=rejected, row_id_by_seq=row_id_by_seq
        )

    @staticmethod
    def _fenced(kind: str, fact: Payload, refusal: FenceRefusal) -> tuple[bool, None]:
        """A fact the write fence refused — rejected in the ack like a route-token refusal,
        so the runner's drain acks it and moves on."""
        _log.warning("fact fenced", kind=kind, chunk_id=fact.text("chunk_id"), detail=refusal.detail)
        return False, None

    def _apply(
        self, runner_id: str, kind: str, payload: dict[str, object], *, route_token_mode: str
    ) -> tuple[bool, int | None]:
        """Apply one fact; ``(True, row_id)`` on success — ``row_id`` is the freshly-written
        row's own id only for a kind whose id is not already in its own
        payload (``escalation.recorded``/``event.recorded``), else ``None``. ``(False,
        None)`` on an unknown kind or a route-token rejection."""
        now = self._clock.now()
        fact = Payload(payload)
        gated = requires_route_token(kind, fact.text("chunk_id"))
        if gated is None:
            return False, None
        if gated and not self._route_token_ok(fact.require_text("chunk_id"), runner_id, fact, mode=route_token_mode):
            return False, None
        if kind == LEASE_MINTED:
            refusal = self._route.record_lease_minted(
                fact.require_text("chunk_id"),
                epoch=fact.require_number("epoch"),
                claimant=Claimant(runner_id, fact.text("lease_id")),
                at=now,
            )
            if refusal is not None:
                return self._fenced(kind, fact, refusal)
            return True, None
        if kind == ESCALATION_RECORDED:
            escalation_id = self._escalations.record_escalation(
                fact.require_text("chunk_id"),
                epoch=fact.require_number("epoch"),
                admission=EpochAdmission.AT_OR_ABOVE,
                claimant=Claimant(runner_id, fact.text("lease_id")),
                takeover_command=fact.string("takeover_command"),
                wrapped_takeover_command=fact.string("wrapped_takeover_command"),
                cause=fact.text("cause"),
                detail=fact.text("detail"),
                at=now,
            )
            if isinstance(escalation_id, FenceRefusal):
                return self._fenced(kind, fact, escalation_id)
            return True, escalation_id
        if kind == QUESTION_ASKED:
            # The runner authors the question_id so it can poll the answer back.
            refusal = self._questions.record_question(
                question_id=fact.require_text("question_id"),
                chunk_id=fact.require_text("chunk_id"),
                node_id=fact.text("node_id"),
                session_id=fact.text("session_id"),
                harness_id=fact.text("harness_id"),
                runner_id=runner_id,
                epoch=fact.require_number("epoch"),
                admission=EpochAdmission.AT_OR_ABOVE,
                claimant=Claimant(runner_id, fact.text("lease_id")),
                question=fact.require_text("question"),
                options=fact.strings("options"),
                asked_at=fact.instant("asked_at", now),
            )
            if refusal is not None:
                return self._fenced(kind, fact, refusal)
            return True, None
        if kind == USAGE_RECORDED:
            # No epoch fence and no route-token gate: trailing-epoch spend is real and attributed to its
            # own epoch (pinned in tests/test_usage_facts_ingest.py, test_route_token_authz.py).
            self._usage.record_usage(
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
            )
            return True, None
        if kind == EVENT_RECORDED:
            # Neither epoch-fenced nor route-token-gated: an event from a fenced-out or
            # dying worker is exactly the signal this log exists to surface. `chunk_id` is optional.
            wire_kind = admitted_event_kind(fact.require_text("kind"), fact.require_text("severity"))
            if wire_kind is None:
                _log.warning("event fact outside the closed vocabulary", kind=fact.require_text("kind"))
                return False, None
            event_id = self._events.record(
                kind=wire_kind,
                runner_id=runner_id,
                chunk_id=fact.text("chunk_id"),
                lease_id=fact.text("lease_id"),
                node_name=fact.text("node_name"),
                message=fact.string("message"),
                detail=fact.mapping("detail"),
                at=now,
            )
            return True, event_id
        if kind == ANSWER_DELIVERED:
            # Records that the resume-with-answer ran; derives no status of its own.
            return self._deliver_answer(fact.require_text("question_id"), fact.require_text("chunk_id"), now=now), None
        if kind == EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED:
            # Advisory, refresh-in-place per (runner_id, slug).
            identity = subscription_identity(fact)
            if identity is None:
                return False, None
            slug, name = identity
            self._fleet.record_external_usage(
                runner_id,
                slug=slug,
                name=name,
                sampled_at=fact.instant("sampled_at", now),
                windows_json=_external_usage_windows_json(fact.get("windows", []), runner_id=runner_id, slug=slug),
                at=now,
            )
            return True, None
        if kind == EXTERNAL_SUBSCRIPTION_USAGE_MISSED:
            # Advisory sibling to the sampled fact above — refresh-in-place per
            # (runner_id, slug), in its own table, never touching the sample row.
            identity = subscription_identity(fact)
            reason = SampleMissReason.recognized(fact.get("reason"))
            if identity is None or reason is None:
                return False, None
            slug, name = identity
            self._fleet.record_external_usage_miss(
                runner_id,
                slug=slug,
                name=name,
                missed_at=fact.instant("missed_at", now),
                reason=reason,
                at=now,
            )
            return True, None
        if kind in (RUNNER_LOCALLY_PAUSED, RUNNER_LOCALLY_RESUMED):
            # Runner-scoped and hub-read-only. Stamped off the payload — when the runner decided, not
            # when its buffer drained, which may be an outage later.
            pause = LocalPause.of(kind, fact, now=now)
            local_pause_id = self._fleet.record_local_pause(
                runner_id, paused=pause.paused, at=pause.at, by=pause.by, reason=pause.reason
            )
            return True, local_pause_id
        _log.warning("unknown runner fact kind", kind=kind)
        return False, None

    def _deliver_answer(self, question_id: str, chunk_id: str, *, now: datetime) -> bool:
        """Land an ``answer.delivered`` as :meth:`NodeQuestion.delivery` decides: written, a
        replay that writes nothing, or rejected in the ack (an unknown question too)."""
        question: NodeQuestion | None = self._questions.get_question(question_id)
        if question is None:
            _log.warning("answer delivery for an unknown question", question_id=question_id)
            return False
        facts = ChunkFacts.or_default(self._facts.load_facts(question.chunk_id))
        delivery, detail = question.delivery(
            chunk_id=chunk_id, superseded_by_restart=facts.restarted_past(question.epoch)
        )
        if delivery is QuestionDelivery.REFUSE:
            _log.warning("answer delivery rejected", question_id=question_id, detail=detail)
            return False
        if delivery is QuestionDelivery.RECORD:
            self._questions.record_answer_delivered(question_id=question_id, chunk_id=chunk_id, at=now)
        return True

    def _route_token_ok(self, chunk_id: str, runner_id: str, fact: Payload, *, mode: str) -> bool:
        """Route-token authorization for a chunk-scoped, fence-advancing fact — the
        buffered-push counterpart of ``apply.py``'s own check. A chunk the
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
