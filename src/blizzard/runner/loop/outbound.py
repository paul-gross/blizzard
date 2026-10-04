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
from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.auth.tokens import IReadTokenRepository
from blizzard.runner.domain.asks import OpenAsk
from blizzard.runner.domain.leases import Lease
from blizzard.runner.domain.outbound import IWriteOutboundRepository, event_payload
from blizzard.runner.events.publisher import IRunnerEventPublisher
from blizzard.wire.completion import CompletionSubmission
from blizzard.wire.decision import DecisionSubmission
from blizzard.wire.facts import (
    ANSWER_DELIVERED,
    ESCALATION_RECORDED,
    EVENT_RECORDED,
    LEASE_MINTED,
    QUESTION_ASKED,
)

# The two kinds the flusher handles specially; every other kind flushes to POST /events.
COMPLETION_KIND = "completion.submitted"
DECISION_KIND = "decision.submitted"

_EVENT_COMMAND_FAILED: EventLogKind = "command-failed"
_EVENT_TRANSCRIPT_TRUNCATED: EventLogKind = "transcript-truncated"


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
        payload = {
            "chunk_id": chunk_id,
            "epoch": epoch,
            "lease_id": lease_id,
            "route_token": self.ctx.stores.tokens.route_token(chunk_id),
        }
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
        payload = {
            "chunk_id": lease.chunk_id,
            "epoch": lease.epoch,
            "lease_id": lease.lease_id,
            "takeover_command": takeover,
            "wrapped_takeover_command": wrapped_takeover,
            "cause": str(cause),
            "detail": detail,
            "route_token": self.ctx.stores.tokens.route_token(lease.chunk_id),
        }
        self._enqueue(ESCALATION_RECORDED, lease.chunk_id, lease.lease_id, payload, at)

    def question_asked(self, lease: Lease, ask: OpenAsk, *, at: datetime) -> None:
        payload = {
            "question_id": ask.question_id,
            "chunk_id": lease.chunk_id,
            "node_id": lease.node_id,
            "session_id": ask.session_id or lease.session_id,
            "harness_id": ask.harness_id or lease.harness_id,
            "epoch": lease.epoch,
            "lease_id": lease.lease_id,
            "question": ask.question,
            "options": ask.options,
            "asked_at": iso_utc(ask.asked_at),
            "route_token": self.ctx.stores.tokens.route_token(lease.chunk_id),
        }
        self._enqueue(QUESTION_ASKED, lease.chunk_id, lease.lease_id, payload, at)

    def answer_delivered(self, lease: Lease, question_id: str, *, at: datetime) -> None:
        payload = {"chunk_id": lease.chunk_id, "question_id": question_id}
        self._enqueue(ANSWER_DELIVERED, lease.chunk_id, lease.lease_id, payload, at)

    def completion(self, lease: Lease, submission: CompletionSubmission, *, at: datetime) -> None:
        payload = {"submission": submission.model_dump(mode="json")}
        self._enqueue(COMPLETION_KIND, lease.chunk_id, lease.lease_id, payload, at)

    def decision(self, lease: Lease, submission: DecisionSubmission, *, at: datetime) -> None:
        payload = {"submission": submission.model_dump(mode="json")}
        self._enqueue(DECISION_KIND, lease.chunk_id, lease.lease_id, payload, at)

    def command_failed(
        self, *, chunk_id: str | None, lease_id: str | None, node_name: str | None, command: str, stderr_tail: str
    ) -> None:
        """A captured spawn/verify/env-prep command failure, surfaced as a
        ``warning`` operational event that rides no closure and alters no control flow."""
        self.event(
            kind=_EVENT_COMMAND_FAILED,
            chunk_id=chunk_id,
            lease_id=lease_id,
            node_name=node_name,
            message=f"command failed: {command}",
            detail={"command": command, "stderr_tail": stderr_tail[-2000:] if stderr_tail else ""},
            at=self.ctx.clock.now(),
        )

    def transcript_truncated(self, *, chunk_id: str, segment_id: str, reason: str, at: datetime) -> None:
        """A transcript segment stopped shipping content, surfaced as a
        ``warning`` operational event on the FACT lane — the issue-#125 precedent.
        Truncation is never silent: it is also a field on the segment itself."""
        self.event(
            kind=_EVENT_TRANSCRIPT_TRUNCATED,
            chunk_id=chunk_id,
            lease_id=None,
            node_name=None,
            message=f"transcript segment {segment_id} truncated — {reason}",
            detail={"segment_id": segment_id, "reason": reason},
            at=at,
        )

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
