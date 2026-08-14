"""The runner event broker — its typed ``publish_*`` wrappers and event-type vocabulary over
the kind-agnostic core (D1, blizzard#317) shared with the hub.

The history/replay/live-fanout machinery underneath — id minting, the bounded ring, the
per-connection queues — lives in :mod:`blizzard.foundation.events.broker`; this module
owns only what is runner-specific: the event-type names, their payload shapes, and the
``publish_*`` helpers a mutation seam calls (Phase 3 — no call site is wired here yet)."""

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
    """The runner's typed ``publish_*`` wrappers over the shared broker core.

    The id-minting/ring/replay/fan-out machinery lives in
    :class:`blizzard.foundation.events.broker.EventBroker`; this subclass adds only
    the runner's own event shapes. Phase 3 wires the call sites; this class is
    complete enough for that phase to use without further design."""

    def publish_lease_changed(
        self,
        lease_id: str,
        chunk_id: str,
        *,
        cause: LeaseChangeCause,
        node_name: str | None = None,
        key: str | None = None,
    ) -> int:
        """A lease was minted or closed."""
        payload = LeaseChangedPayload(
            lease_id=lease_id, chunk_id=chunk_id, cause=cause, node_name=node_name, key=key
        ).to_payload()
        return self.publish(LEASE_CHANGED, payload)

    def publish_ask_changed(
        self, lease_id: str, chunk_id: str, question_id: str, *, cause: AskChangeCause, key: str | None = None
    ) -> int:
        """A worker's ask was recorded, or its answer landed."""
        payload = AskChangedPayload(
            lease_id=lease_id, chunk_id=chunk_id, question_id=question_id, cause=cause, key=key
        ).to_payload()
        return self.publish(ASK_CHANGED, payload)

    def publish_escalation_changed(
        self, chunk_id: str, *, cause: EscalationChangeCause, lease_id: str | None = None, key: str | None = None
    ) -> int:
        """A chunk escalated to needs-human, or that escalation was superseded/closed."""
        payload = EscalationChangedPayload(chunk_id=chunk_id, cause=cause, lease_id=lease_id, key=key).to_payload()
        return self.publish(ESCALATION_CHANGED, payload)

    def publish_takeover_changed(
        self, chunk_id: str, takeover_id: str, *, cause: TakeoverChangeCause, key: str | None = None
    ) -> int:
        """An operator takeover opened or closed."""
        payload = TakeoverChangedPayload(chunk_id=chunk_id, takeover_id=takeover_id, cause=cause, key=key).to_payload()
        return self.publish(TAKEOVER_CHANGED, payload)

    def publish_environment_changed(
        self, chunk_id: str, environment_id: str, *, cause: EnvironmentChangeCause, key: str | None = None
    ) -> int:
        """An environment-pool slot was bound to a chunk, or released."""
        payload = EnvironmentChangedPayload(
            chunk_id=chunk_id, environment_id=environment_id, cause=cause, key=key
        ).to_payload()
        return self.publish(ENVIRONMENT_CHANGED, payload)

    def publish_fact_changed(
        self, *, seq: int, kind: str, chunk_id: str | None, lease_id: str | None, key: str | None = None
    ) -> int:
        """A hub-bound fact was enqueued onto the outbound buffer."""
        payload = FactChangedPayload(seq=seq, kind=kind, chunk_id=chunk_id, lease_id=lease_id, key=key).to_payload()
        return self.publish(FACT_CHANGED, payload)
