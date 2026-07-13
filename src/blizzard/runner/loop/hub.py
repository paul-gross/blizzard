"""The hub-client seam — the runner's outbound edge to the hub HTTP API (D-012).

The runner is operator-directed and talks to the hub outbound-only: it peeks the
ready queue, claims a route (acquisition, D-080), submits node-step completions
(D-036), re-reads the idempotent envelope (D-090), and polls a chunk's derived
status to learn a hub node's terminal outcome (D-066). This Protocol is the seam;
the httpx adapter under ``internal/`` is the reference binding, and loop tests
inject a fake or an ``httpx.MockTransport``-backed client — no live hub needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from blizzard.wire.chunk import ChunkDetail
from blizzard.wire.completion import CompletionSubmission
from blizzard.wire.decision import DecisionSubmission
from blizzard.wire.envelope import ApplyResponse, NodeEnvelope
from blizzard.wire.facts import RunnerFactAck, RunnerFactBatch
from blizzard.wire.question import QuestionView
from blizzard.wire.queue import QueuePeekResponse
from blizzard.wire.route import RouteClaim, RouteClaimConflict, RouteClaimResponse


class HubClientError(RuntimeError):
    """A hub call failed at the transport level (unreachable, 5xx, malformed body).

    A 409 route conflict is **not** an error — it is an expected race outcome
    returned as :class:`RouteClaimOutcome`. This type is only raised for genuine
    transport failures the loop treats as "hub unreachable, try next tick".
    """


@dataclass(frozen=True)
class RouteClaimOutcome:
    """The result of a route claim: exactly one of ``claimed`` / ``conflict`` set."""

    claimed: RouteClaimResponse | None = None
    conflict: RouteClaimConflict | None = None

    @property
    def won(self) -> bool:
        return self.claimed is not None


class IHubClient(Protocol):
    """The runner's client of the hub API (D-012). Outbound-only."""

    def peek_queue(self) -> QueuePeekResponse:
        """``GET /api/queue/peek`` — the hub-ordered ready queue (D-080)."""
        ...

    def claim_route(self, claim: RouteClaim) -> RouteClaimOutcome:
        """``POST /api/routes`` — claim work; 409 loses the race (D-080)."""
        ...

    def submit_completion(self, chunk_id: str, submission: CompletionSubmission) -> ApplyResponse:
        """``POST /api/chunks/{id}/completions`` — the atomic, epoch-fenced write (D-036)."""
        ...

    def submit_decision(self, chunk_id: str, submission: DecisionSubmission) -> ApplyResponse:
        """``POST /api/chunks/{id}/decisions`` — a runner-config gate parks the chunk (D-032)."""
        ...

    def push_facts(self, batch: RunnerFactBatch) -> RunnerFactAck:
        """``POST /api/events`` — store-and-forward fact push, seq-idempotent (D-069)."""
        ...

    def get_envelope(self, chunk_id: str) -> NodeEnvelope:
        """``GET /api/chunks/{id}/envelope`` — the idempotent envelope re-read (D-090)."""
        ...

    def get_chunk(self, chunk_id: str) -> ChunkDetail:
        """``GET /api/chunks/{id}`` — the chunk's derived status, polled at a hub node (D-066)."""
        ...

    def get_question(self, question_id: str) -> QuestionView:
        """``GET /api/questions/{id}`` — the runner's answer poll ([ask-answer.md]).

        A parked chunk's runner polls its forwarded question by id; once ``answered`` is
        true the answer is delivered by resuming the dormant session around it."""
        ...

    def report_lease(self, chunk_id: str, *, epoch: int, runner_id: str) -> None:
        """``POST /api/chunks/{id}/leases`` — a ``lease.minted`` fact (D-044).

        Reported at every node-step spawn so the hub's epoch fence tracks the runner's:
        a chunk that visits a second runner node (review) submits its completion under a
        fresh epoch the hub must already know, and a requeue's mint is what closes an
        escalation by supersession (D-035/D-067)."""
        ...

    def report_escalation(self, chunk_id: str, *, epoch: int, runner_id: str, takeover_command: str) -> None:
        """``POST /api/chunks/{id}/escalations`` — retries exhausted (D-009).

        Lands the escalation at the hub so the chunk derives ``needs_human`` fleet-wide,
        carrying the pasteable takeover command (design/harness-adapters.md)."""
        ...
