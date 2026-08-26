"""The runner event broker — its typed ``publish_*`` wrappers and event-type vocabulary over
the kind-agnostic core (D1, blizzard#317) shared with the hub. The history/replay/live-fanout
machinery — id minting, the bounded ring, per-connection queues — lives in
:mod:`blizzard.foundation.events.broker`; this module owns only what is runner-specific: the
event-type names, their payload shapes, and the ``publish_*`` helpers each mutation seam
calls (wired in Phase 3, see ``tests/runner_event_census.py``)."""

from __future__ import annotations

from blizzard.foundation.events.broker import EventBroker as _EventBroker
from blizzard.wire.sse_runner import (
    AskChangeCause,
    AskChangedPayload,
    EnvironmentChangeCause,
    EnvironmentChangedPayload,
    EscalationChangeCause,
    EscalationChangedPayload,
    FactChangedPayload,
    LeaseChangeCause,
    LeaseChangedPayload,
    TakeoverChangeCause,
    TakeoverChangedPayload,
)

# SSE event-type names — the runner's live vocabulary.
LEASE_CHANGED = "lease-changed"
ASK_CHANGED = "ask-changed"
ESCALATION_CHANGED = "escalation-changed"
TAKEOVER_CHANGED = "takeover-changed"
ENVIRONMENT_CHANGED = "environment-changed"
FACT_CHANGED = "fact-changed"

#: Every event-type name the broker can publish. This tuple, not the bare constants
#: above, is the broker's declared vocabulary.
EVENT_TYPES: tuple[str, ...] = (
    LEASE_CHANGED,
    ASK_CHANGED,
    ESCALATION_CHANGED,
    TAKEOVER_CHANGED,
    ENVIRONMENT_CHANGED,
    FACT_CHANGED,
)


class EventBroker(_EventBroker):
    """The runner's typed ``publish_*`` wrappers over the shared broker core
    (:class:`blizzard.foundation.events.broker.EventBroker`), adding only the
    runner's own event shapes."""

    def publish_lease_changed(self, lease_id: str, chunk_id: str, *, cause: LeaseChangeCause) -> int:
        """A lease was minted or closed."""
        payload = LeaseChangedPayload(lease_id=lease_id, chunk_id=chunk_id, cause=cause).to_payload()
        return self.publish(LEASE_CHANGED, payload)

    def publish_ask_changed(self, lease_id: str, chunk_id: str, question_id: str, *, cause: AskChangeCause) -> int:
        """A worker's ask was recorded, or its answer landed."""
        payload = AskChangedPayload(
            lease_id=lease_id, chunk_id=chunk_id, question_id=question_id, cause=cause
        ).to_payload()
        return self.publish(ASK_CHANGED, payload)

    def publish_escalation_changed(
        self, chunk_id: str, *, cause: EscalationChangeCause, lease_id: str | None = None
    ) -> int:
        """A chunk escalated to needs-human, or that escalation was superseded/closed."""
        payload = EscalationChangedPayload(chunk_id=chunk_id, cause=cause, lease_id=lease_id).to_payload()
        return self.publish(ESCALATION_CHANGED, payload)

    def publish_takeover_changed(self, chunk_id: str, takeover_id: str, *, cause: TakeoverChangeCause) -> int:
        """An operator takeover opened or closed."""
        payload = TakeoverChangedPayload(chunk_id=chunk_id, takeover_id=takeover_id, cause=cause).to_payload()
        return self.publish(TAKEOVER_CHANGED, payload)

    def publish_environment_changed(self, chunk_id: str, environment_id: str, *, cause: EnvironmentChangeCause) -> int:
        """An environment-pool slot was bound to a chunk, or released."""
        payload = EnvironmentChangedPayload(chunk_id=chunk_id, environment_id=environment_id, cause=cause).to_payload()
        return self.publish(ENVIRONMENT_CHANGED, payload)

    def publish_fact_changed(self, *, seq: int, kind: str, chunk_id: str | None, lease_id: str | None) -> int:
        """A hub-bound fact was enqueued onto the outbound buffer."""
        payload = FactChangedPayload(seq=seq, kind=kind, chunk_id=chunk_id, lease_id=lease_id).to_payload()
        return self.publish(FACT_CHANGED, payload)
