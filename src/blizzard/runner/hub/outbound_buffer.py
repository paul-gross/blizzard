"""The hub-bound store-and-forward outbound buffer repository seam."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.foundation.event_log import EVENT_LOG_SEVERITY, EventLogKind
from blizzard.foundation.roles import domain_model, dto
from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.leases import Lease
from blizzard.runner.leases.asks import OpenAsk

__all__ = [
    "COMMAND_FAILED_STDERR_TAIL",
    "COMPLETION_KIND",
    "DECISION_KIND",
    "OUTBOUND_TRANSITIONS",
    "SUBMISSION_KINDS",
    "BufferedFact",
    "IReadOutboundRepository",
    "IWriteOutboundRepository",
    "OutboundEventFields",
    "OutboundFactEntry",
    "OutboundFactState",
    "answer_delivered_payload",
    "command_failed_event",
    "escalation_payload",
    "event_payload",
    "lease_minted_payload",
    "question_asked_payload",
    "submission_payload",
    "transcript_truncated_event",
]

#: The two submission kinds the drain flushes to their own routes; every other flushes to ``POST /events``.
COMPLETION_KIND = "completion.submitted"
DECISION_KIND = "decision.submitted"
SUBMISSION_KINDS: tuple[str, ...] = (COMPLETION_KIND, DECISION_KIND)

#: How much of a failed command's stderr a ``command-failed`` event keeps — its tail.
COMMAND_FAILED_STDERR_TAIL = 2000

#: Where one buffered fact stands: awaiting delivery, or acked (delivered or semantically rejected).
OutboundFactState = Literal["pending", "acked"]

#: The writes a buffered fact takes from each state: one ack, from pending; re-acking keeps the first ``acked_at``.
OUTBOUND_TRANSITIONS: dict[OutboundFactState, frozenset[Literal["ack"]]] = {
    "pending": frozenset({"ack"}),
    "acked": frozenset(),
}


@domain_model
@dataclass(frozen=True)
class BufferedFact:
    """One pending hub-bound fact in the store-and-forward buffer."""

    seq: int
    kind: str
    chunk_id: str | None
    lease_id: str | None
    payload: str
    created_at: datetime

    @property
    def is_submission(self) -> bool:
        """A completion or decision — flushed to its own route, never to ``POST /events``."""
        return self._is_submission()

    def _is_submission(self) -> bool:
        return self.kind in SUBMISSION_KINDS

    @property
    def is_completion(self) -> bool:
        return self._is_completion()

    def _is_completion(self) -> bool:
        return self.kind == COMPLETION_KIND


@dto
@dataclass(frozen=True)
class OutboundFactEntry:
    """One hub-bound fact off the outbound buffer, acked or not. The same table as
    :class:`BufferedFact`, read as a ledger: ``acked_at`` kept, ``payload`` dropped."""

    seq: int
    kind: str
    chunk_id: str | None
    lease_id: str | None
    created_at: datetime
    acked_at: datetime | None


def event_payload(
    *,
    kind: EventLogKind,
    chunk_id: str | None,
    lease_id: str | None,
    node_name: str | None,
    message: str,
    detail: Mapping[str, object] | None,
) -> dict[str, object]:
    """The ``event.recorded`` payload — one shape for every operational event this runner buffers."""
    return {
        "severity": EVENT_LOG_SEVERITY[kind],
        "kind": kind,
        "chunk_id": chunk_id,
        "lease_id": lease_id,
        "node_name": node_name,
        "message": message,
        "detail": detail,
    }


def lease_minted_payload(chunk_id: str, lease_id: str, *, epoch: int, route_token: str | None) -> dict[str, object]:
    """The ``lease.minted`` payload — the fence input the hub's completion check consumes."""
    return {"chunk_id": chunk_id, "epoch": epoch, "lease_id": lease_id, "route_token": route_token}


def escalation_payload(
    lease: Lease,
    *,
    takeover: str,
    wrapped_takeover: str,
    cause: EscalationCause,
    detail: str,
    route_token: str | None,
) -> dict[str, object]:
    """The ``escalation.recorded`` payload — both takeover strings and why it was raised."""
    return {
        "chunk_id": lease.chunk_id,
        "epoch": lease.epoch,
        "lease_id": lease.lease_id,
        "takeover_command": takeover,
        "wrapped_takeover_command": wrapped_takeover,
        "cause": str(cause),
        "detail": detail,
        "route_token": route_token,
    }


def question_asked_payload(lease: Lease, ask: OpenAsk, *, route_token: str | None) -> dict[str, object]:
    """The ``question.asked`` payload. The ask's own session and harness win; the lease's
    stand in for an ask that recorded none."""
    return {
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
        "route_token": route_token,
    }


def answer_delivered_payload(lease: Lease, question_id: str) -> dict[str, object]:
    return {"chunk_id": lease.chunk_id, "question_id": question_id}


def submission_payload(submission_json: Mapping[str, object]) -> dict[str, object]:
    """A completion's or decision's payload: the submission, already rendered to JSON."""
    return {"submission": dict(submission_json)}


@dto
@dataclass(frozen=True)
class OutboundEventFields:
    """The message and detail one operational event carries, before :func:`event_payload` frames it."""

    kind: EventLogKind
    message: str
    detail: Mapping[str, object]


def command_failed_event(*, command: str, stderr_tail: str) -> OutboundEventFields:
    """A captured command failure: its command and the last :data:`COMMAND_FAILED_STDERR_TAIL`
    characters of its stderr."""
    return OutboundEventFields(
        kind="command-failed",
        message=f"command failed: {command}",
        detail={"command": command, "stderr_tail": stderr_tail[-COMMAND_FAILED_STDERR_TAIL:] if stderr_tail else ""},
    )


def transcript_truncated_event(*, segment_id: str, reason: str) -> OutboundEventFields:
    """A segment that stopped shipping content — truncation is never silent."""
    return OutboundEventFields(
        kind="transcript-truncated",
        message=f"transcript segment {segment_id} truncated — {reason}",
        detail={"segment_id": segment_id, "reason": reason},
    )


class IReadOutboundRepository(Protocol):
    """Read-only outbound-buffer queries (held by read-path edges)."""

    def pending_submission_lease_ids(self) -> set[str]:
        """Lease ids with an unacked ``completion.submitted`` or ``decision.submitted``
        fact in the buffer.

        The judged-and-buffered skip set, so a node-step's outcome is elicited exactly once
        while the flush is pending."""
        ...

    def pending_outbound(self, *, limit: int | None = None) -> list[BufferedFact]:
        """The unacked outbound buffer, FIFO by seq; unbounded when ``limit`` is ``None``.

        A given ``limit`` bounds the query itself, not just what the caller iterates — a
        large backlog's full payload set is otherwise materialized before any per-run bound
        the caller applies is ever consulted."""
        ...

    def pending_outbound_count(self) -> int:
        """How many facts are unacked, across the WHOLE backlog — never truncated by
        :meth:`pending_outbound`'s own ``limit``, and never materializing a payload row
        just to count it."""
        ...

    def recent_outbound(self, limit: int) -> list[OutboundFactEntry]:
        """The newest ``limit`` outbound facts, acked or not, newest first — the local fact log."""
        ...


class IWriteOutboundRepository(IReadOutboundRepository, Protocol):
    """Read-write outbound-buffer store — held only by the domain."""

    def enqueue_outbound(
        self, *, kind: str, chunk_id: str | None, lease_id: str | None, payload: str, created_at: datetime
    ) -> int:
        """Append a hub-bound fact to the store-and-forward buffer; return its seq."""
        ...

    def ack_outbound(self, seq: int, *, acked_at: datetime) -> None:
        """Mark a buffered fact delivered — a semantic rejection acks too. An already-acked
        fact keeps its first ``acked_at`` (:data:`OUTBOUND_TRANSITIONS`)."""
        ...

    def ack_outbound_batch(self, seqs: list[int], *, acked_at: datetime) -> None:
        """Mark every seq in ``seqs`` delivered, in one transaction, so a
        crash mid-batch never acks part of one delivered run. An already-acked seq keeps
        its first ``acked_at``."""
        ...

    def prune_outbound(self, *, now: datetime) -> int:
        """Delete acked rows older than the store's own retention window, but
        only below the lowest still-pending seq — an acked row interleaved above a pending
        one always survives, so the retained buffer stays gapless from the pending floor
        upward. Returns the number of rows pruned."""
        ...
