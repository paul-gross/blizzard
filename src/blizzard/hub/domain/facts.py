"""Runner-reported fact intake — lease mints, escalations, and the rest of the runner's outbound facts.

:class:`RunnerFactsService` is the direct single-fact intake; :class:`FactIngestService` is the batched
store-and-forward push, idempotent against a per-runner **high-water mark**. Landing each lease mint is
what keeps the epoch fence in lockstep across a chunk's successive node-steps. Both hold the **write**
chunk seams each fact lands on (``bzh:controller-read-only``) and stamp landing time from the injected
clock."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import as_utc
from blizzard.hub.config import ROUTE_TOKEN_WARN
from blizzard.hub.domain.chunks.escalations import IWriteChunkEscalationsRepository
from blizzard.hub.domain.chunks.events import IWriteChunkEventsRepository
from blizzard.hub.domain.chunks.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunks.questions import IWriteChunkQuestionsRepository
from blizzard.hub.domain.chunks.route import IWriteChunkRouteRepository
from blizzard.hub.domain.chunks.usage import IWriteChunkUsageRepository
from blizzard.hub.domain.registry import LEGACY_ANTHROPIC_SLUG, FleetService
from blizzard.hub.domain.route_auth import RouteToken
from blizzard.hub.domain.work import ChunkFacts
from blizzard.wire.facts import (
    ANSWER_DELIVERED,
    ESCALATION_RECORDED,
    EVENT_RECORDED,
    EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED,
    LEASE_MINTED,
    QUESTION_ASKED,
    RUNNER_LOCALLY_PAUSED,
    RUNNER_LOCALLY_RESUMED,
    USAGE_RECORDED,
    RunnerFactAck,
    RunnerFactBatch,
)

_log = get_logger("blizzard.hub.facts")

# The chunk-scoped, fence-advancing kinds gated on intake (issue #84b): a fabricated one from a
# non-holder must not advance the fence or open a decision. Runner-scoped kinds are never gated.
_ROUTE_TOKEN_GATED_KINDS = frozenset({LEASE_MINTED, ESCALATION_RECORDED, QUESTION_ASKED})


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
        """A usage fact's ``cost_usd`` — ``None`` stays ``None`` (no envelope), never fabricated."""
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
        value = self.body.get(key)
        if isinstance(value, str):
            try:
                return as_utc(datetime.fromisoformat(value))
            except ValueError:
                return fallback
        return fallback


class RunnerFactsService:
    """Land runner-reported ``lease.minted`` / ``escalation.recorded`` facts."""

    def __init__(
        self, *, route: IWriteChunkRouteRepository, escalations: IWriteChunkEscalationsRepository, clock: IClock
    ) -> None:
        self._route = route
        self._escalations = escalations
        self._clock = clock

    def record_lease_minted(self, chunk_id: str, *, epoch: int, runner_id: str) -> None:
        """Land a runner's ``lease.minted`` — advances the fence's latest epoch."""
        self._route.record_lease(chunk_id, epoch=epoch, runner_id=runner_id, at=self._clock.now())

    def record_escalation(
        self, chunk_id: str, *, epoch: int, takeover_command: str, wrapped_takeover_command: str = ""
    ) -> int:
        """Land a runner's ``escalation.recorded`` — the chunk derives ``needs_human``.

        Returns the freshly-written ``escalations.id`` (issue #213's activity-feed key)."""
        return self._escalations.record_escalation(
            chunk_id,
            epoch=epoch,
            takeover_command=takeover_command,
            wrapped_takeover_command=wrapped_takeover_command,
            at=self._clock.now(),
        )


@dataclass(frozen=True)
class FactIngestResult:
    """:meth:`FactIngestService.ingest`'s own return — the wire :class:`RunnerFactAck` plus, per
    freshly-applied fact (issue #213), the id of the row it wrote. ``row_id_by_seq`` carries an entry
    only for a kind whose own id is not already in its payload. Not a wire type."""

    ack: RunnerFactAck
    row_id_by_seq: dict[int, int]


class FactIngestService:
    """Apply a runner's batched pushed facts idempotently against its high-water mark. Most facts are
    chunk-scoped and land through one of the seams above; ``fleet`` is here for the runner-scoped ones
    (issue #43)."""

    def __init__(
        self,
        *,
        facts: IReadChunkFactsRepository,
        route: IWriteChunkRouteRepository,
        escalations: IWriteChunkEscalationsRepository,
        questions: IWriteChunkQuestionsRepository,
        usage: IWriteChunkUsageRepository,
        events: IWriteChunkEventsRepository,
        fleet: FleetService,
        clock: IClock,
    ) -> None:
        self._facts = facts
        self._route = route
        self._escalations = escalations
        self._questions = questions
        self._usage = usage
        self._events = events
        self._fleet = fleet
        self._clock = clock

    def ingest(self, batch: RunnerFactBatch, *, route_token_mode: str = ROUTE_TOKEN_WARN) -> FactIngestResult:
        mark = self._route.runner_high_water(batch.runner_id)
        applied: list[int] = []
        already: list[int] = []
        rejected: list[int] = []
        row_id_by_seq: dict[int, int] = {}

        for fact in sorted(batch.facts, key=lambda f: f.seq):
            if fact.seq <= mark:
                already.append(fact.seq)
                continue
            ok, row_id = self._apply(batch.runner_id, fact.kind, fact.payload, route_token_mode=route_token_mode)
            if not ok:
                # A contract mismatch, not an idempotency skip (issue #84b): do not advance the mark
                # past it, and name it in the ack.
                rejected.append(fact.seq)
                continue
            mark = fact.seq
            applied.append(fact.seq)
            if row_id is not None:
                row_id_by_seq[fact.seq] = row_id

        if applied:
            self._route.set_runner_high_water(batch.runner_id, seq=mark, at=self._clock.now())
        _log.info(
            "runner facts ingested",
            runner_id=batch.runner_id,
            high_water=mark,
            applied=len(applied),
            already=len(already),
            rejected=len(rejected),
        )
        ack = RunnerFactAck(
            runner_id=batch.runner_id,
            high_water=mark,
            applied=applied,
            already_applied=already,
            rejected=rejected,
        )
        return FactIngestResult(ack=ack, row_id_by_seq=row_id_by_seq)

    def _apply(
        self, runner_id: str, kind: str, payload: dict[str, object], *, route_token_mode: str
    ) -> tuple[bool, int | None]:
        """Apply one fact; ``(True, row_id)`` on success — ``row_id`` is the freshly-written
        row's own id (issue #213) only for a kind whose id is not already in its own
        payload (``escalation.recorded``/``event.recorded``), else ``None``. ``(False,
        None)`` on an unknown kind or a route-token rejection."""
        now = self._clock.now()
        fact = Payload(payload)
        if kind in _ROUTE_TOKEN_GATED_KINDS:
            chunk_id = fact.text("chunk_id")
            if chunk_id is None or not self._route_token_ok(chunk_id, runner_id, fact, mode=route_token_mode):
                return False, None
        if kind == LEASE_MINTED:
            self._route.record_lease(
                fact.require_text("chunk_id"),
                epoch=fact.require_number("epoch"),
                runner_id=runner_id,
                at=now,
            )
            return True, None
        if kind == ESCALATION_RECORDED:
            escalation_id = self._escalations.record_escalation(
                fact.require_text("chunk_id"),
                epoch=fact.require_number("epoch"),
                takeover_command=fact.string("takeover_command"),
                wrapped_takeover_command=fact.string("wrapped_takeover_command"),
                at=now,
            )
            return True, escalation_id
        if kind == QUESTION_ASKED:
            # The runner authors the question_id so it can poll the answer back.
            self._questions.record_question(
                question_id=fact.require_text("question_id"),
                chunk_id=fact.require_text("chunk_id"),
                node_id=fact.text("node_id"),
                session_id=fact.text("session_id"),
                runner_id=runner_id,
                epoch=fact.require_number("epoch"),
                question=fact.require_text("question"),
                options=fact.strings("options"),
                asked_at=fact.instant("asked_at", now),
            )
            return True, None
        if kind == USAGE_RECORDED:
            # No epoch fence and no route-token gate: trailing-epoch spend is real and attributed to its
            # own epoch (issue #84b; pinned in tests/test_usage_facts_ingest.py, test_route_token_authz.py).
            self._usage.record_usage(
                fact.require_text("chunk_id"),
                node_id=fact.require_text("node_id"),
                epoch=fact.require_number("epoch"),
                runner_id=runner_id,
                kind=fact.require_text("kind"),
                model=fact.require_text("model"),
                input_tokens=fact.require_number("input_tokens"),
                output_tokens=fact.require_number("output_tokens"),
                cache_read_tokens=fact.require_number("cache_read_tokens"),
                cache_create_tokens=fact.require_number("cache_create_tokens"),
                cost_usd=fact.amount("cost_usd"),
                at=now,
            )
            return True, None
        if kind == EVENT_RECORDED:
            # Neither epoch-fenced nor route-token-gated (issue #125): an event from a fenced-out or
            # dying worker is exactly the signal this log exists to surface. `chunk_id` is optional.
            event_id = self._events.record_event(
                severity=fact.require_text("severity"),
                kind=fact.require_text("kind"),
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
            self._questions.record_answer_delivered(
                question_id=fact.require_text("question_id"), chunk_id=fact.require_text("chunk_id"), at=now
            )
            return True, None
        if kind == EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED:
            # Runner-scoped and hub-read-only: an advisory fact no status derives from. Refresh-in-place
            # per (runner_id, slug) (`bzh:facts-not-status`'s stated exception) — only each
            # subscription's latest sample is of interest. `slug` defaults to the legacy
            # slug for a fact somehow missing it, though every fact carries one post-#436
            # phase 2; `name` defaults to `slug` itself for a fact predating phase 3's
            # additive `name` field.
            slug = fact.text("slug") or LEGACY_ANTHROPIC_SLUG
            self._fleet.record_external_usage(
                runner_id,
                slug=slug,
                name=fact.text("name") or slug,
                sampled_at=fact.instant("sampled_at", now),
                windows_json=json.dumps(fact.get("windows", [])),
                at=now,
            )
            return True, None
        if kind in (RUNNER_LOCALLY_PAUSED, RUNNER_LOCALLY_RESUMED):
            # Runner-scoped and hub-read-only. Stamped off the payload — when the runner decided, not
            # when its buffer drained, which may be an outage later.
            local_pause_id = self._fleet.record_local_pause(
                runner_id,
                paused=kind == RUNNER_LOCALLY_PAUSED,
                at=fact.instant("at", now),
                by=fact.string("by", "operator"),
                reason=fact.text("reason"),
            )
            return True, local_pause_id
        _log.warning("unknown runner fact kind", kind=kind)
        return False, None

    def _route_token_ok(self, chunk_id: str, runner_id: str, fact: Payload, *, mode: str) -> bool:
        """Route-token authorization for a chunk-scoped, fence-advancing fact (issue
        #84b) — the buffered-push counterpart of ``apply.py``'s own check. A chunk the
        hub has never minted (``load_facts`` returns ``None``, e.g. a malformed/stale
        payload) falls back to an empty :class:`ChunkFacts`, which
        :class:`RouteToken` already rejects as having no live route."""
        facts = self._facts.load_facts(chunk_id) or ChunkFacts(minted=True)
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
