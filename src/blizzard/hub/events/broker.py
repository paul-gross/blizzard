"""The hub event broker — its typed ``publish_*`` wrappers and event-type vocabulary over
the kind-agnostic core (D1, blizzard#317) shared with the runner.

The history/replay/live-fanout machinery underneath — id minting, the bounded ring, the
per-connection queues — lives in :mod:`blizzard.foundation.events.broker`; this module
owns only what is hub-specific: the event-type names, their payload shapes, and the
``publish_*`` helpers each mutation seam calls."""

from __future__ import annotations

from blizzard.foundation.events.broker import EventBroker as _EventBroker
from blizzard.wire.sse import (
    ChunkChangeCause,
    ChunkChangedPayload,
    DecisionOpenedPayload,
    DecisionResolvedPayload,
    EventLoggedPayload,
    QuestionAnsweredPayload,
    QuestionAskedPayload,
    QueueChangedPayload,
    RunnerChangedPayload,
    RunnerChangeKind,
)

# SSE event-type names — the board's live vocabulary.
CHUNK_CHANGED = "chunk-changed"
QUESTION_ASKED = "question-asked"
QUESTION_ANSWERED = "question-answered"
DECISION_OPENED = "decision-opened"
DECISION_RESOLVED = "decision-resolved"
QUEUE_CHANGED = "queue-changed"
RUNNER_CHANGED = "runner-changed"
EVENT_LOGGED = "event-logged"

#: Every event-type name the broker can publish (issue #235). This tuple, not the bare
#: constants above, is the broker's declared vocabulary.
EVENT_TYPES: tuple[str, ...] = (
    CHUNK_CHANGED,
    QUESTION_ASKED,
    QUESTION_ANSWERED,
    DECISION_OPENED,
    DECISION_RESOLVED,
    QUEUE_CHANGED,
    RUNNER_CHANGED,
    EVENT_LOGGED,
)


class EventBroker(_EventBroker):
    """The hub's typed ``publish_*`` wrappers over the shared broker core.

    The id-minting/ring/replay/fan-out machinery lives in
    :class:`blizzard.foundation.events.broker.EventBroker`; this subclass adds only
    the hub's own event shapes."""

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
        key: str | None = None,
    ) -> int:
        """A chunk's derived status changed.

        Optionals are added to the payload only when supplied, never serialized as
        ``null`` (issue #212). ``key`` (issue #213) is the table-qualified natural key of
        the fact this frame describes, absent when there is no such fact."""
        payload = ChunkChangedPayload(
            chunk_id=chunk_id,
            status=status,
            prev_status=prev_status,
            prev_node=prev_node,
            node=node,
            runner_id=runner_id,
            cause=cause,
            graph_id=graph_id,
            key=key,
        ).to_payload()
        return self.publish(CHUNK_CHANGED, payload)

    def publish_question_asked(self, chunk_id: str, question_id: str, *, key: str | None = None) -> int:
        """A ``question.asked`` landed — the chunk parks ``waiting_on_human``."""
        payload = QuestionAskedPayload(chunk_id=chunk_id, question_id=question_id, key=key).to_payload()
        return self.publish(QUESTION_ASKED, payload)

    def publish_question_answered(self, chunk_id: str, question_id: str, *, key: str | None = None) -> int:
        """A ``question.answered`` landed — the chunk leaves ``waiting_on_human``."""
        payload = QuestionAnsweredPayload(chunk_id=chunk_id, question_id=question_id, key=key).to_payload()
        return self.publish(QUESTION_ANSWERED, payload)

    def publish_decision_opened(self, chunk_id: str, decision_id: str, *, key: str | None = None) -> int:
        """A gate ``decision.submitted`` opened — a human choice is awaited."""
        payload = DecisionOpenedPayload(chunk_id=chunk_id, decision_id=decision_id, key=key).to_payload()
        return self.publish(DECISION_OPENED, payload)

    def publish_decision_resolved(self, chunk_id: str, decision_id: str, *, key: str | None = None) -> int:
        """A ``decision.resolved`` landed — the holding runner will advance the chunk."""
        payload = DecisionResolvedPayload(chunk_id=chunk_id, decision_id=decision_id, key=key).to_payload()
        return self.publish(DECISION_RESOLVED, payload)

    def publish_queue_changed(self) -> int:
        """The ready queue's membership or order changed — the board re-peeks.

        Carries no ``key`` (issue #213): a reorder writes N rows with no per-row news, so
        there is no single durable fact this frame could name."""
        return self.publish(QUEUE_CHANGED, QueueChangedPayload().to_payload())

    def publish_runner_changed(
        self,
        runner_id: str,
        *,
        kind: RunnerChangeKind,
        by: str | None = None,
        reason: str | None = None,
        key: str | None = None,
    ) -> int:
        """A runner's registry state changed — ``kind`` names which change (issue #151).

        ``by`` rides the four pause/resume kinds and ``reason`` the runner-local pair.
        ``key`` (issue #213) names the pause-family fact's identity, absent on
        ``registered``/``heartbeat``, which have no fact table."""
        payload = RunnerChangedPayload(runner_id=runner_id, kind=kind, by=by, reason=reason, key=key).to_payload()
        return self.publish(RUNNER_CHANGED, payload)

    def publish_event_logged(
        self, *, severity: str, kind: str, chunk_id: str | None, runner_id: str, key: str | None = None
    ) -> int:
        """An operational event landed in the event log (issue #125). The frame carries
        only identifying fields; the row itself is read back off ``GET /api/events``.
        ``key`` (issue #213) names the ``event_log`` row's own id."""
        payload = EventLoggedPayload(
            severity=severity, kind=kind, chunk_id=chunk_id, runner_id=runner_id, key=key
        ).to_payload()
        return self.publish(EVENT_LOGGED, payload)
