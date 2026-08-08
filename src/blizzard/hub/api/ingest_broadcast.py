"""Re-broadcasting a landed fact batch on the SSE stream."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.hub.api.chunk_events import ChunkChanged
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.facts import FactIngestResult
from blizzard.hub.events.broker import ChunkChangeCause
from blizzard.wire.facts import (
    ANSWER_DELIVERED,
    ESCALATION_RECORDED,
    EVENT_RECORDED,
    EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED,
    LEASE_MINTED,
    QUESTION_ASKED,
    RUNNER_LOCALLY_PAUSED,
    RUNNER_LOCALLY_RESUMED,
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
        hot path (issue #212)."""
        changes: dict[str, ChunkChanged] = {}
        for fact in batch.facts:
            chunk_id = fact.payload.get("chunk_id")
            if isinstance(chunk_id, str) and chunk_id not in changes:
                changes[chunk_id] = ChunkChanged.before(services, chunk_id)
        return cls(services=services, batch=batch, changes=changes)

    def publish(self, result: FactIngestResult) -> None:
        applied = set(result.ack.applied)
        for fact in self.batch.facts:
            if fact.seq in applied:
                self._publish_one(fact, result.row_id_by_seq.get(fact.seq))

    def _publish_one(self, fact: RunnerFact, row_id: int | None) -> None:
        """The runner-scoped kinds dispatch first: carrying no ``chunk_id``, the chunk arm drops them."""
        if fact.kind in (RUNNER_LOCALLY_PAUSED, RUNNER_LOCALLY_RESUMED):
            self._runner_pause(fact, row_id)
        elif fact.kind == EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED:
            self.services.events.publish_runner_changed(self.batch.runner_id, kind="external-usage")
        elif fact.kind == EVENT_RECORDED:
            self._event_logged(fact, row_id)
        else:
            chunk_id = fact.payload.get("chunk_id")
            if isinstance(chunk_id, str):
                self._chunk_changed(fact, row_id, chunk_id)

    def _runner_pause(self, fact: RunnerFact, row_id: int | None) -> None:
        """The frame carries the fact's own ``by``/``reason`` (issue #151), with the same ``by``
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

    def _event_logged(self, fact: RunnerFact, row_id: int | None) -> None:
        chunk_id = fact.payload.get("chunk_id")
        self.services.events.publish_event_logged(
            severity=str(fact.payload.get("severity", "")),
            kind=str(fact.payload.get("kind", "")),
            chunk_id=chunk_id if isinstance(chunk_id, str) else None,
            runner_id=self.batch.runner_id,
            key=f"event_log:{row_id}" if row_id is not None else None,
        )

    def _chunk_changed(self, fact: RunnerFact, row_id: int | None, chunk_id: str) -> None:
        """Published on the fact rather than on a status *change*, so a fact that moves no status
        (``answer.delivered``, issue #165) still stales the chunk read."""
        key = self._dedupe_key(fact, row_id, chunk_id)
        question_id = fact.payload.get("question_id")
        if fact.kind == QUESTION_ASKED and isinstance(question_id, str):
            self.services.events.publish_question_asked(chunk_id, question_id, key=key)
        change = self.changes.get(chunk_id) or ChunkChanged.of(self.services, chunk_id, prev_status=None)
        change.publish(cause=_CAUSE_BY_FACT_KIND.get(fact.kind), key=key)

    def _dedupe_key(self, fact: RunnerFact, row_id: int | None, chunk_id: str) -> str | None:
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
            # (issue #213), so a lost-ack replay dedupes against the live route.
            route = self.services.chunks.route_of(chunk_id)
            if route is not None and route.route_id is not None:
                return f"route_created:{route.route_id}"
        return None
