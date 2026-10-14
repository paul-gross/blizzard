"""The runner event broker — its typed ``publish_*`` wrappers and event-type vocabulary over
the kind-agnostic core shared with the hub. The history/replay/live-fanout
machinery — id minting, the bounded ring, per-connection queues — lives in
:mod:`blizzard.foundation.events.broker`; this module owns only what is runner-specific: the
event-type names, their payload shapes, and the ``publish_*`` helpers each mutation seam
calls — each wiring is covered by ``tests/runner_event_census.py``."""

from __future__ import annotations

from blizzard.foundation.events.broker import EventBroker as _EventBroker
from blizzard.foundation.runner_event_types import (
    AskChangeCause,
    EnvironmentChangeCause,
    EscalationChangeCause,
    LeaseChangeCause,
    RunnerEventType,
    TakeoverChangeCause,
)
from blizzard.wire.sse_runner import (
    AskChangedPayload,
    EnvironmentChangedPayload,
    EscalationChangedPayload,
    FactChangedPayload,
    LeaseChangedPayload,
    TakeoverChangedPayload,
)

#: Every event-type name the broker can publish — its declared vocabulary.
EVENT_TYPES: tuple[str, ...] = tuple(RunnerEventType)


class EventBroker(_EventBroker):
    """The runner's typed ``publish_*`` wrappers over the shared broker core
    (:class:`blizzard.foundation.events.broker.EventBroker`), adding only the
    runner's own event shapes."""

    def publish_lease_changed(self, lease_id: str, chunk_id: str, *, cause: LeaseChangeCause, runner_id: str) -> int:
        """A lease was minted or closed."""
        payload = LeaseChangedPayload(
            runner_id=runner_id, lease_id=lease_id, chunk_id=chunk_id, cause=cause
        ).to_payload()
        return self.publish(RunnerEventType.LEASE_CHANGED, payload)

    def publish_ask_changed(
        self, lease_id: str, chunk_id: str, question_id: str, *, cause: AskChangeCause, runner_id: str
    ) -> int:
        """A worker's ask was recorded, or its answer landed."""
        payload = AskChangedPayload(
            runner_id=runner_id, lease_id=lease_id, chunk_id=chunk_id, question_id=question_id, cause=cause
        ).to_payload()
        return self.publish(RunnerEventType.ASK_CHANGED, payload)

    def publish_escalation_changed(
        self, chunk_id: str, *, cause: EscalationChangeCause, runner_id: str, lease_id: str | None = None
    ) -> int:
        """A chunk escalated to needs-human, or that escalation was superseded/closed."""
        payload = EscalationChangedPayload(
            runner_id=runner_id, chunk_id=chunk_id, cause=cause, lease_id=lease_id
        ).to_payload()
        return self.publish(RunnerEventType.ESCALATION_CHANGED, payload)

    def publish_takeover_changed(
        self, chunk_id: str, takeover_id: str, *, cause: TakeoverChangeCause, runner_id: str
    ) -> int:
        """An operator takeover opened or closed."""
        payload = TakeoverChangedPayload(
            runner_id=runner_id, chunk_id=chunk_id, takeover_id=takeover_id, cause=cause
        ).to_payload()
        return self.publish(RunnerEventType.TAKEOVER_CHANGED, payload)

    def publish_environment_changed(self, chunk_id: str, environment_id: str, *, cause: EnvironmentChangeCause) -> int:
        """An environment-pool slot was bound to a chunk, or released."""
        payload = EnvironmentChangedPayload(chunk_id=chunk_id, environment_id=environment_id, cause=cause).to_payload()
        return self.publish(RunnerEventType.ENVIRONMENT_CHANGED, payload)

    def publish_fact_changed(
        self, *, seq: int, kind: str, chunk_id: str | None, lease_id: str | None, runner_id: str
    ) -> int:
        """A hub-bound fact was enqueued onto the outbound buffer."""
        payload = FactChangedPayload(
            runner_id=runner_id, seq=seq, kind=kind, chunk_id=chunk_id, lease_id=lease_id
        ).to_payload()
        return self.publish(RunnerEventType.FACT_CHANGED, payload)


class RunnerEventPublisher:
    """One runner's :class:`~blizzard.runner.events.publisher.IRunnerEventPublisher` over the host's one broker,
    stamping its runner's id on every runner-scoped event; ``environment-changed`` is the host's and carries none."""

    def __init__(self, broker: EventBroker, runner_id: str) -> None:
        self._broker = broker
        self._runner_id = runner_id

    @classmethod
    def over(cls, broker: EventBroker | None, runner_id: str) -> RunnerEventPublisher | None:
        """``runner_id``'s publisher over ``broker``; ``None`` where no broker is wired."""
        return cls(broker, runner_id) if broker is not None else None

    def publish_lease_changed(self, lease_id: str, chunk_id: str, *, cause: LeaseChangeCause) -> int:
        return self._broker.publish_lease_changed(lease_id, chunk_id, cause=cause, runner_id=self._runner_id)

    def publish_ask_changed(self, lease_id: str, chunk_id: str, question_id: str, *, cause: AskChangeCause) -> int:
        return self._broker.publish_ask_changed(lease_id, chunk_id, question_id, cause=cause, runner_id=self._runner_id)

    def publish_escalation_changed(
        self, chunk_id: str, *, cause: EscalationChangeCause, lease_id: str | None = None
    ) -> int:
        return self._broker.publish_escalation_changed(
            chunk_id, cause=cause, runner_id=self._runner_id, lease_id=lease_id
        )

    def publish_takeover_changed(self, chunk_id: str, takeover_id: str, *, cause: TakeoverChangeCause) -> int:
        return self._broker.publish_takeover_changed(chunk_id, takeover_id, cause=cause, runner_id=self._runner_id)

    def publish_environment_changed(self, chunk_id: str, environment_id: str, *, cause: EnvironmentChangeCause) -> int:
        return self._broker.publish_environment_changed(chunk_id, environment_id, cause=cause)

    def publish_fact_changed(self, *, seq: int, kind: str, chunk_id: str | None, lease_id: str | None) -> int:
        return self._broker.publish_fact_changed(
            seq=seq, kind=kind, chunk_id=chunk_id, lease_id=lease_id, runner_id=self._runner_id
        )
