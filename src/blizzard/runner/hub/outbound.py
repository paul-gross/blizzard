"""The hub-bound facts this runner buffers, and the payload shape each one takes."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.foundation.event_log import EventLogKind
from blizzard.foundation.fact_kinds import (
    ANSWER_DELIVERED,
    ESCALATION_RECORDED,
    EVENT_RECORDED,
    LEASE_MINTED,
    QUESTION_ASKED,
)
from blizzard.runner.auth.tokens import IReadTokenRepository
from blizzard.runner.events.publisher import IRunnerEventPublisher
from blizzard.runner.hub.node_steps import (
    completion_of,
    completion_submission,
    decision_submission,
    gate_submission_of,
)
from blizzard.runner.hub.outbound_buffer import (
    COMPLETION_KIND,
    DECISION_KIND,
    BufferedFact,
    IWriteOutboundRepository,
    OutboundEventFields,
    answer_delivered_payload,
    command_failed_event,
    escalation_payload,
    event_payload,
    lease_minted_payload,
    question_asked_payload,
    submission_payload,
    transcript_truncated_event,
)
from blizzard.runner.leases import Lease
from blizzard.runner.leases.asks import OpenAsk
from blizzard.runner.node_steps.submissions import Completion, GateSubmission
from blizzard.wire.completion import CompletionSubmission
from blizzard.wire.decision import DecisionSubmission

__all__ = [
    "COMPLETION_KIND",
    "DECISION_KIND",
    "OutboundContext",
    "OutboundFacts",
    "OutboundStores",
    "buffered_completion",
    "buffered_gate",
]


class OutboundStores(Protocol):
    @property
    def outbound(self) -> IWriteOutboundRepository: ...
    @property
    def tokens(self) -> IReadTokenRepository: ...


class OutboundContext(Protocol):
    @property
    def stores(self) -> OutboundStores: ...
    @property
    def clock(self) -> IClock: ...
    @property
    def events(self) -> IRunnerEventPublisher | None: ...


@dataclass(frozen=True)
class OutboundFacts:
    """Every fact this runner sends the hub — one method per kind, each rendering its own
    payload into the single store-and-forward buffer PULL drains in FIFO order."""

    ctx: OutboundContext

    def lease_minted(self, chunk_id: str, lease_id: str, *, epoch: int, at: datetime) -> None:
        """Buffered ahead of any completion minted under it: the drain is strict FIFO, and this
        is the fence input the hub's completion check consumes."""
        payload = lease_minted_payload(chunk_id, lease_id, epoch=epoch, route_token=self._route_token(chunk_id))
        self._enqueue(LEASE_MINTED, chunk_id, lease_id, payload, at)

    def escalation(
        self,
        lease: Lease,
        *,
        takeover: str,
        wrapped_takeover: str,
        cause: EscalationCause,
        detail: str,
        at: datetime,
    ) -> None:
        """Carries both takeover strings and why the escalation was raised."""
        payload = escalation_payload(
            lease,
            takeover=takeover,
            wrapped_takeover=wrapped_takeover,
            cause=cause,
            detail=detail,
            route_token=self._route_token(lease.chunk_id),
        )
        self._enqueue(ESCALATION_RECORDED, lease.chunk_id, lease.lease_id, payload, at)

    def question_asked(self, lease: Lease, ask: OpenAsk, *, at: datetime) -> None:
        payload = question_asked_payload(lease, ask, route_token=self._route_token(lease.chunk_id))
        self._enqueue(QUESTION_ASKED, lease.chunk_id, lease.lease_id, payload, at)

    def answer_delivered(self, lease: Lease, question_id: str, *, at: datetime) -> None:
        payload = answer_delivered_payload(lease, question_id)
        self._enqueue(ANSWER_DELIVERED, lease.chunk_id, lease.lease_id, payload, at)

    def completion(self, lease: Lease, completion: Completion, *, at: datetime) -> None:
        """Buffered as the wire body the drain later submits, so a row written before a redeploy
        drains unchanged after it."""
        payload = submission_payload(completion_submission(completion).model_dump(mode="json"))
        self._enqueue(COMPLETION_KIND, lease.chunk_id, lease.lease_id, payload, at)

    def decision(self, lease: Lease, gate: GateSubmission, *, at: datetime) -> None:
        payload = submission_payload(decision_submission(gate).model_dump(mode="json"))
        self._enqueue(DECISION_KIND, lease.chunk_id, lease.lease_id, payload, at)

    def command_failed(
        self,
        *,
        chunk_id: str | None,
        lease_id: str | None,
        node_name: str | None,
        command: str,
        stderr_tail: str,
        at: datetime | None = None,
    ) -> None:
        """A captured spawn/verify/env-prep command failure, surfaced as a
        ``warning`` operational event that rides no closure and alters no control flow. An
        omitted ``at`` reads the clock."""
        fields = command_failed_event(command=command, stderr_tail=stderr_tail)
        when = at if at is not None else self.ctx.clock.now()
        self._event(fields, chunk_id=chunk_id, lease_id=lease_id, node_name=node_name, at=when)

    def transcript_truncated(self, *, chunk_id: str, segment_id: str, reason: str, at: datetime) -> None:
        """A transcript segment stopped shipping content, surfaced as a
        ``warning`` operational event on the FACT lane."""
        fields = transcript_truncated_event(segment_id=segment_id, reason=reason)
        self._event(fields, chunk_id=chunk_id, lease_id=None, node_name=None, at=at)

    def _event(
        self,
        fields: OutboundEventFields,
        *,
        chunk_id: str | None,
        lease_id: str | None,
        node_name: str | None,
        at: datetime,
    ) -> None:
        self.event(
            kind=fields.kind,
            chunk_id=chunk_id,
            lease_id=lease_id,
            node_name=node_name,
            message=fields.message,
            detail=fields.detail,
            at=at,
        )

    def _route_token(self, chunk_id: str) -> str | None:
        return self.ctx.stores.tokens.route_token(chunk_id)

    def event(
        self,
        *,
        kind: EventLogKind,
        chunk_id: str | None,
        lease_id: str | None,
        node_name: str | None,
        message: str,
        detail: Mapping[str, object] | None,
        at: datetime,
    ) -> None:
        payload = event_payload(
            kind=kind, chunk_id=chunk_id, lease_id=lease_id, node_name=node_name, message=message, detail=detail
        )
        self._enqueue(EVENT_RECORDED, chunk_id, lease_id, payload, at)

    def _enqueue(
        self, kind: str, chunk_id: str | None, lease_id: str | None, payload: Mapping[str, object], at: datetime
    ) -> None:
        seq = self.ctx.stores.outbound.enqueue_outbound(
            kind=kind, chunk_id=chunk_id, lease_id=lease_id, payload=json.dumps(payload), created_at=at
        )
        if self.ctx.events is not None:
            self.ctx.events.publish_fact_changed(seq=seq, kind=kind, chunk_id=chunk_id, lease_id=lease_id)


def buffered_completion(fact: BufferedFact) -> Completion:
    """The completion a buffered ``COMPLETION_KIND`` fact carries."""
    return completion_of(CompletionSubmission.model_validate(json.loads(fact.payload)["submission"]))


def buffered_gate(fact: BufferedFact) -> GateSubmission:
    """The gate submission a buffered ``DECISION_KIND`` fact carries."""
    return gate_submission_of(DecisionSubmission.model_validate(json.loads(fact.payload)["submission"]))
