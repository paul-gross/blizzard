"""Re-broadcasting a landed fact batch on the SSE stream."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from blizzard.hub.api.chunk_events import ChunkChanged, ChunkFrameState
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.facts import FactIngestResult
from blizzard.hub.events.broker import ChunkChangeCause
from blizzard.wire.facts import (
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
    RunnerFact,
    RunnerFactBatch,
)

#: The ``chunk-changed`` cause for each chunk-scoped fact kind an ingest lands.
_CAUSE_BY_FACT_KIND: dict[str, ChunkChangeCause] = {
    QUESTION_ASKED: "question-asked",
    ANSWER_DELIVERED: "question-answered",
    ESCALATION_RECORDED: "escalated",
    LEASE_MINTED: "claimed",
}


class _RunnerArm(StrEnum):
    """How ``_publish_one`` publishes a runner-scoped fact kind."""

    PAUSE = "pause"
    EXTERNAL_USAGE = "external-usage"
    LOGGED = "logged"


#: The runner-scoped fact kinds — carrying no ``chunk_id`` — and the arm each dispatches to. The
#: chunk arm drops every kind named here, so a kind lands in exactly one place.
_RUNNER_ARM_BY_FACT_KIND: dict[str, _RunnerArm] = {
    RUNNER_LOCALLY_PAUSED: _RunnerArm.PAUSE,
    RUNNER_LOCALLY_RESUMED: _RunnerArm.PAUSE,
    EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED: _RunnerArm.EXTERNAL_USAGE,
    EXTERNAL_SUBSCRIPTION_USAGE_MISSED: _RunnerArm.EXTERNAL_USAGE,
    EVENT_RECORDED: _RunnerArm.LOGGED,
}

#: The chunk-scoped fact kinds — each routed through the chunk arm on its payload's ``chunk_id``.
_CHUNK_SCOPED_FACT_KINDS = frozenset(
    {LEASE_MINTED, ESCALATION_RECORDED, QUESTION_ASKED, ANSWER_DELIVERED, USAGE_RECORDED}
)


@dataclass(frozen=True)
class IngestBroadcast:
    """One batch's stream side-effects, held across the ingest that lands it.

    Built *before* the batch lands, so it carries each touched chunk's prior status, and
    published after, once the ack names which facts were freshly applied."""

    services: HubServices
    batch: RunnerFactBatch
    changes: dict[str, ChunkChanged]

    @classmethod
    def before_ingest(cls, services: HubServices, batch: RunnerFactBatch) -> IngestBroadcast:
        """One pre-mutation snapshot per distinct chunk, reused across the batch — this is the
        hot path."""
        chunk_ids = [chunk_id for fact in batch.facts if isinstance(chunk_id := fact.payload.get("chunk_id"), str)]
        changes = ChunkChanged.before_many(services, chunk_ids)
        return cls(services=services, batch=batch, changes=changes)

    def publish(self, result: FactIngestResult) -> None:
        applied = set(result.ack.applied)
        applied_facts = [fact for fact in self.batch.facts if fact.seq in applied]
        chunk_ids = [cid for fact in applied_facts if (cid := self._chunk_arm_id(fact)) is not None]
        states = ChunkFrameState.load_many(self.services, chunk_ids)
        for fact in applied_facts:
            self._publish_one(fact, result.row_id_by_seq.get(fact.seq), states)

    @staticmethod
    def _chunk_arm_id(fact: RunnerFact) -> str | None:
        """The chunk id ``_publish_one``'s chunk arm dispatches ``fact`` to, or ``None`` for
        a runner-scoped kind or a chunk-scoped payload missing its id."""
        if fact.kind in _RUNNER_ARM_BY_FACT_KIND:
            return None
        chunk_id = fact.payload.get("chunk_id")
        return chunk_id if isinstance(chunk_id, str) else None

    def _publish_one(self, fact: RunnerFact, row_id: int | None, states: dict[str, ChunkFrameState]) -> None:
        """The runner-scoped kinds dispatch first: carrying no ``chunk_id``, the chunk arm drops them."""
        arm = _RUNNER_ARM_BY_FACT_KIND.get(fact.kind)
        if arm is _RunnerArm.PAUSE:
            self._runner_pause(fact, row_id)
        elif arm is _RunnerArm.EXTERNAL_USAGE:
            self.services.events.publish_runner_changed(self.batch.runner_id, kind="external-usage")
        elif arm is _RunnerArm.LOGGED:
            pass  # already published by EventLogService.record
        else:
            chunk_id = self._chunk_arm_id(fact)
            if chunk_id is not None:
                self._chunk_changed(fact, row_id, chunk_id, states[chunk_id])

    def _runner_pause(self, fact: RunnerFact, row_id: int | None) -> None:
        """The frame carries the fact's own ``by``/``reason``, with the same ``by``
        default applied when the fact omits one."""
        by = fact.payload.get("by")
        reason = fact.payload.get("reason")
        self.services.events.publish_runner_changed(
            self.batch.runner_id,
            kind="locally-paused" if fact.kind == RUNNER_LOCALLY_PAUSED else "locally-resumed",
            by=by if isinstance(by, str) else "operator",
            reason=reason if isinstance(reason, str) else None,
            key=f"runner_local_pause_facts:{row_id}" if row_id is not None else None,
        )

    def _chunk_changed(self, fact: RunnerFact, row_id: int | None, chunk_id: str, state: ChunkFrameState) -> None:
        """Published on the fact rather than on a status *change*, so a fact that moves no status
        (``answer.delivered``) still stales the chunk read."""
        key = self._dedupe_key(fact, row_id, state)
        question_id = fact.payload.get("question_id")
        if fact.kind == QUESTION_ASKED and isinstance(question_id, str):
            self.services.events.publish_question_asked(chunk_id, question_id, key=key)
        change = self.changes[chunk_id]
        change.publish_from(state, cause=_CAUSE_BY_FACT_KIND.get(fact.kind), key=key)

    def _dedupe_key(self, fact: RunnerFact, row_id: int | None, state: ChunkFrameState) -> str | None:
        """The stream key a lost-ack replay of this fact dedupes against."""
        if fact.kind in (QUESTION_ASKED, ANSWER_DELIVERED):
            question_id = fact.payload.get("question_id")
            if not isinstance(question_id, str):
                return None
            table = "questions" if fact.kind == QUESTION_ASKED else "question_answers"
            return f"{table}:{question_id}"
        if fact.kind == ESCALATION_RECORDED:
            return f"escalations:{row_id}" if row_id is not None else None
        if fact.kind == LEASE_MINTED:
            # This site writes a `lease_facts` row, but its `claimed` cause maps to `route_created`
            # so a lost-ack replay dedupes against the live route.
            route = state.route
            if route is not None and route.route_id is not None:
                return f"route_created:{route.route_id}"
        return None
