"""The hub event broker — its typed ``publish_*`` wrappers and event-type vocabulary over
the kind-agnostic core shared with the runner. The history/replay/
live-fanout machinery — id minting, the bounded ring, per-connection queues — lives in
:mod:`blizzard.foundation.events.broker`; this module owns only what is hub-specific: the
event-type names, their payload shapes, and each mutation seam's ``publish_*`` helper."""

from __future__ import annotations

from blizzard.foundation.event_log import EventLogSeverity
from blizzard.foundation.events.broker import EventBroker as _EventBroker
from blizzard.foundation.hub_event_types import ChunkChangeCause, HubEventType, RunnerChangeKind
from blizzard.wire.sse import (
    ChunkChangedPayload,
    DecisionOpenedPayload,
    DecisionResolvedPayload,
    EventLoggedPayload,
    QuestionAnsweredPayload,
    QuestionAskedPayload,
    QueueChangedPayload,
    RunnerChangedPayload,
)

#: Every event-type name the broker can publish — its declared vocabulary.
EVENT_TYPES: tuple[str, ...] = tuple(HubEventType)


class EventBroker(_EventBroker):
    """The hub's typed ``publish_*`` wrappers over the shared broker core
    (:class:`blizzard.foundation.events.broker.EventBroker`), adding only the
    hub's own event shapes."""

    def publish_chunk_changed(
        self,
        chunk_id: str,
        status: str,
        *,
        prev_status: str | None = None,
        prev_node: str | None = None,
        node: str | None = None,
        runner_id: str | None = None,
        cause: ChunkChangeCause | None = None,
        graph_id: str | None = None,
        by: str | None = None,
        key: str | None = None,
    ) -> int:
        """A chunk's derived status changed.

        Optionals are added to the payload only when supplied, never serialized as
        ``null``. ``key`` is the table-qualified natural key of
        the fact this frame describes, absent when there is no such fact. ``by`` rides
        the ``deleted`` cause, mirroring :meth:`publish_runner_changed`."""
        payload = ChunkChangedPayload(
            chunk_id=chunk_id,
            status=status,
            prev_status=prev_status,
            prev_node=prev_node,
            node=node,
            runner_id=runner_id,
            cause=cause,
            graph_id=graph_id,
            by=by,
            key=key,
        ).to_payload()
        return self.publish(HubEventType.CHUNK_CHANGED, payload)

    def publish_question_asked(self, chunk_id: str, question_id: str, *, key: str | None = None) -> int:
        """A ``question.asked`` landed — the chunk parks ``waiting_on_human``."""
        payload = QuestionAskedPayload(chunk_id=chunk_id, question_id=question_id, key=key).to_payload()
        return self.publish(HubEventType.QUESTION_ASKED, payload)

    def publish_question_answered(self, chunk_id: str, question_id: str, *, key: str | None = None) -> int:
        """A ``question.answered`` landed — the chunk leaves ``waiting_on_human``."""
        payload = QuestionAnsweredPayload(chunk_id=chunk_id, question_id=question_id, key=key).to_payload()
        return self.publish(HubEventType.QUESTION_ANSWERED, payload)

    def publish_decision_opened(self, chunk_id: str, decision_id: str, *, key: str | None = None) -> int:
        """A gate ``decision.submitted`` opened — a human choice is awaited."""
        payload = DecisionOpenedPayload(chunk_id=chunk_id, decision_id=decision_id, key=key).to_payload()
        return self.publish(HubEventType.DECISION_OPENED, payload)

    def publish_decision_resolved(self, chunk_id: str, decision_id: str, *, key: str | None = None) -> int:
        """A ``decision.resolved`` landed — the holding runner will advance the chunk."""
        payload = DecisionResolvedPayload(chunk_id=chunk_id, decision_id=decision_id, key=key).to_payload()
        return self.publish(HubEventType.DECISION_RESOLVED, payload)

    def publish_queue_changed(self) -> int:
        """The ready queue's membership or order changed — the board re-peeks.

        Carries no ``key``: a reorder writes N rows with no per-row news, so
        there is no single durable fact this frame could name."""
        return self.publish(HubEventType.QUEUE_CHANGED, QueueChangedPayload().to_payload())

    def publish_runner_changed(
        self,
        runner_id: str,
        *,
        kind: RunnerChangeKind,
        by: str | None = None,
        reason: str | None = None,
        key: str | None = None,
    ) -> int:
        """A runner's registry state changed — ``kind`` names which change.

        ``by`` rides the four pause/resume kinds and the three retirement kinds, ``reason`` the runner-local pair.
        ``key`` names the pause- or retirement-family fact's identity, absent on
        ``registered``/``heartbeat``, which have no fact table."""
        payload = RunnerChangedPayload(runner_id=runner_id, kind=kind, by=by, reason=reason, key=key).to_payload()
        return self.publish(HubEventType.RUNNER_CHANGED, payload)

    def publish_event_logged(
        self,
        *,
        severity: EventLogSeverity,
        kind: str,
        chunk_id: str | None,
        runner_id: str | None,
        key: str | None = None,
    ) -> int:
        """An operational event landed in the event log. The frame carries
        only identifying fields; the row itself is read back off ``GET /api/events``.
        ``key`` names the ``event_log`` row's own id."""
        payload = EventLoggedPayload(
            severity=severity, kind=kind, chunk_id=chunk_id, runner_id=runner_id, key=key
        ).to_payload()
        return self.publish(HubEventType.EVENT_LOGGED, payload)
