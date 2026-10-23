"""The hub event broker — its typed ``publish_*`` wrappers and event-type vocabulary over
the kind-agnostic core shared with the runner. The history/replay/
live-fanout machinery — id minting, the bounded ring, per-connection queues — lives in
:mod:`blizzard.foundation.events.broker`; this module owns only what is hub-specific: the
event-type names, their payload shapes, and each mutation seam's ``publish_*`` helper."""

from __future__ import annotations

from blizzard.foundation.chunk_status import TERMINAL_STATUSES
from blizzard.foundation.event_log import EventLogSeverity
from blizzard.foundation.events.broker import EventBroker as _EventBroker
from blizzard.foundation.hub_event_types import ChunkChangeCause, HostChangeKind, HubEventType, RunnerChangeKind
from blizzard.wire.sse import (
    ChunkChangedPayload,
    DecisionOpenedPayload,
    DecisionResolvedPayload,
    EventLoggedPayload,
    HostChangedPayload,
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
        runner_name: str | None = None,
        cause: ChunkChangeCause | None = None,
        graph_id: str | None = None,
        by: str | None = None,
        key: str | None = None,
        reason: str | None = None,
        project_id: str | None = None,
    ) -> int:
        """A chunk's derived status changed.

        Optionals are added to the payload only when supplied, never serialized as ``null``. ``key`` is
        the table-qualified natural key of the fact this frame describes, absent when there is no such
        fact. ``by`` rides the ``deleted`` cause, ``reason`` the ``released`` one; ``runner_name`` is
        resolved by the publisher, never here.
        ``project_id`` is the chunk's project, absent only when the chunk is gone. ``terminal`` is judged
        here from ``status`` over :data:`TERMINAL_STATUSES` — the set ``ChunkSummary.terminal`` reads."""
        payload = ChunkChangedPayload(
            chunk_id=chunk_id,
            status=status,
            terminal=status in TERMINAL_STATUSES,
            prev_status=prev_status,
            prev_node=prev_node,
            node=node,
            runner_id=runner_id,
            runner_name=runner_name,
            cause=cause,
            graph_id=graph_id,
            by=by,
            key=key,
            reason=reason,
            project_id=project_id,
        ).to_payload()
        return self.publish(HubEventType.CHUNK_CHANGED, payload)

    def publish_question_asked(
        self, chunk_id: str, question_id: str, *, key: str | None = None, project_id: str | None = None
    ) -> int:
        """A ``question.asked`` landed — the chunk parks ``waiting_on_human``."""
        payload = QuestionAskedPayload(
            chunk_id=chunk_id, question_id=question_id, key=key, project_id=project_id
        ).to_payload()
        return self.publish(HubEventType.QUESTION_ASKED, payload)

    def publish_question_answered(
        self, chunk_id: str, question_id: str, *, key: str | None = None, project_id: str | None = None
    ) -> int:
        """A ``question.answered`` landed — the chunk leaves ``waiting_on_human``."""
        payload = QuestionAnsweredPayload(
            chunk_id=chunk_id, question_id=question_id, key=key, project_id=project_id
        ).to_payload()
        return self.publish(HubEventType.QUESTION_ANSWERED, payload)

    def publish_decision_opened(
        self, chunk_id: str, decision_id: str, *, key: str | None = None, project_id: str | None = None
    ) -> int:
        """A gate ``decision.submitted`` opened — a human choice is awaited."""
        payload = DecisionOpenedPayload(
            chunk_id=chunk_id, decision_id=decision_id, key=key, project_id=project_id
        ).to_payload()
        return self.publish(HubEventType.DECISION_OPENED, payload)

    def publish_decision_resolved(
        self, chunk_id: str, decision_id: str, *, key: str | None = None, project_id: str | None = None
    ) -> int:
        """A ``decision.resolved`` landed — the holding runner will advance the chunk."""
        payload = DecisionResolvedPayload(
            chunk_id=chunk_id, decision_id=decision_id, key=key, project_id=project_id
        ).to_payload()
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
        runner_name: str | None = None,
        by: str | None = None,
        reason: str | None = None,
        key: str | None = None,
    ) -> int:
        """A runner's registry state changed — ``kind`` names which change.

        ``key`` names the pause- or retirement-family fact's identity, absent on
        ``added``/``registered``/``heartbeat``, which have no fact table."""
        payload = RunnerChangedPayload(
            runner_id=runner_id, runner_name=runner_name, kind=kind, by=by, reason=reason, key=key
        ).to_payload()
        return self.publish(HubEventType.RUNNER_CHANGED, payload)

    def publish_host_changed(
        self,
        host_id: str,
        *,
        kind: HostChangeKind,
        host_name: str | None = None,
        by: str | None = None,
        key: str | None = None,
    ) -> int:
        """A host's registry state changed — ``kind`` names which change.

        A host is tenant-wide, so the frame names no project. ``by`` rides every operator kind, ``key`` the
        retirement fact's identity; ``reported`` and ``added`` have no fact table to name. A host reports on every
        tick, so ``reported`` is published only when the report changed what the hub holds."""
        payload = HostChangedPayload(host_id=host_id, host_name=host_name, kind=kind, by=by, key=key).to_payload()
        return self.publish(HubEventType.HOST_CHANGED, payload)

    def publish_event_logged(
        self,
        *,
        severity: EventLogSeverity,
        kind: str,
        chunk_id: str | None,
        runner_id: str | None,
        runner_name: str | None = None,
        key: str | None = None,
        project_id: str | None = None,
    ) -> int:
        """An operational event landed in the event log. The frame carries
        only identifying fields; the row itself is read back off ``GET /api/events``.
        ``key`` names the ``event_log`` row's own id; ``project_id`` names the logged chunk's project."""
        payload = EventLoggedPayload(
            severity=severity,
            kind=kind,
            chunk_id=chunk_id,
            runner_id=runner_id,
            runner_name=runner_name,
            key=key,
            project_id=project_id,
        ).to_payload()
        return self.publish(HubEventType.EVENT_LOGGED, payload)
