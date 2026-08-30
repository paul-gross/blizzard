"""The hub-bound facts this runner buffers, and the payload shape each one takes."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.domain.asks import AskRecord
from blizzard.runner.domain.leases import LeaseRecord
from blizzard.runner.loop.context import LoopContext
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


@dataclass(frozen=True)
class OutboundFacts:
    """Every fact this runner sends the hub — one method per kind, each rendering its own
    payload into the single store-and-forward buffer PULL drains in FIFO order."""

    ctx: LoopContext

    def lease_minted(self, chunk_id: str, lease_id: str, *, epoch: int, at: datetime) -> None:
        """Buffered ahead of any completion minted under it: the drain is strict FIFO, and this
        is the fence input the hub's completion check consumes."""
        payload = {"chunk_id": chunk_id, "epoch": epoch, "route_token": self.ctx.store.route_token(chunk_id)}
        self._enqueue(LEASE_MINTED, chunk_id, lease_id, payload, at)

    def escalation(self, lease: LeaseRecord, *, takeover: str, wrapped_takeover: str, at: datetime) -> None:
        """Carries both takeover strings — the wrapped entry point and the raw pasteable fallback."""
        payload = {
            "chunk_id": lease.chunk_id,
            "epoch": lease.epoch,
            "takeover_command": takeover,
            "wrapped_takeover_command": wrapped_takeover,
            "route_token": self.ctx.store.route_token(lease.chunk_id),
        }
        self._enqueue(ESCALATION_RECORDED, lease.chunk_id, lease.lease_id, payload, at)

    def question_asked(self, lease: LeaseRecord, ask: AskRecord, *, at: datetime) -> None:
        payload = {
            "question_id": ask.question_id,
            "chunk_id": lease.chunk_id,
            "node_id": lease.node_id,
            "session_id": ask.session_id or lease.session_id,
            "epoch": lease.epoch,
            "question": ask.question,
            "options": ask.options,
            "asked_at": iso_utc(ask.asked_at),
            "route_token": self.ctx.store.route_token(lease.chunk_id),
        }
        self._enqueue(QUESTION_ASKED, lease.chunk_id, lease.lease_id, payload, at)

    def answer_delivered(self, lease: LeaseRecord, question_id: str, *, at: datetime) -> None:
        payload = {"chunk_id": lease.chunk_id, "question_id": question_id}
        self._enqueue(ANSWER_DELIVERED, lease.chunk_id, lease.lease_id, payload, at)

    def completion(self, lease: LeaseRecord, submission: CompletionSubmission, *, at: datetime) -> None:
        payload = {"submission": submission.model_dump(mode="json")}
        self._enqueue(COMPLETION_KIND, lease.chunk_id, lease.lease_id, payload, at)

    def decision(self, lease: LeaseRecord, submission: DecisionSubmission, *, at: datetime) -> None:
        payload = {"submission": submission.model_dump(mode="json")}
        self._enqueue(DECISION_KIND, lease.chunk_id, lease.lease_id, payload, at)

    def command_failed(
        self, *, chunk_id: str | None, lease_id: str | None, node_name: str | None, command: str, stderr_tail: str
    ) -> None:
        """A captured spawn/verify/env-prep command failure (issue #125), surfaced as a
        ``warning`` operational event that rides no closure and alters no control flow."""
        self.event(
            chunk_id=chunk_id,
            lease_id=lease_id,
            at=self.ctx.clock.now(),
            payload={
                "severity": "warning",
                "kind": "command-failed",
                "chunk_id": chunk_id,
                "lease_id": lease_id,
                "node_name": node_name,
                "message": f"command failed: {command}",
                "detail": {"command": command, "stderr_tail": stderr_tail[-2000:] if stderr_tail else ""},
            },
        )

    def transcript_truncated(self, *, chunk_id: str, segment_id: str, reason: str, at: datetime) -> None:
        """A transcript segment stopped shipping content (D4, issue #246), surfaced as a
        ``warning`` operational event on the FACT lane — the issue-#125 precedent.
        Truncation is never silent: it is also a field on the segment itself."""
        self.event(
            chunk_id=chunk_id,
            lease_id=None,
            at=at,
            payload={
                "severity": "warning",
                "kind": "transcript-truncated",
                "chunk_id": chunk_id,
                "lease_id": None,
                "node_name": None,
                "message": f"transcript segment {segment_id} truncated — {reason}",
                "detail": {"segment_id": segment_id, "reason": reason},
            },
        )

    def event(self, *, chunk_id: str | None, lease_id: str | None, payload: Mapping[str, object], at: datetime) -> None:
        self._enqueue(EVENT_RECORDED, chunk_id, lease_id, payload, at)

    def _enqueue(
        self, kind: str, chunk_id: str | None, lease_id: str | None, payload: Mapping[str, object], at: datetime
    ) -> None:
        seq = self.ctx.store.enqueue_outbound(
            kind=kind, chunk_id=chunk_id, lease_id=lease_id, payload=json.dumps(payload), created_at=at
        )
        if self.ctx.events is not None:
            self.ctx.events.publish_fact_changed(seq=seq, kind=kind, chunk_id=chunk_id, lease_id=lease_id)
