"""The hub-client seam — the runner's outbound edge to the hub HTTP API.

The runner is operator-directed and talks to the hub outbound-only: it peeks the
ready queue, claims a route (acquisition), submits node-step completions,
re-reads the idempotent envelope, and polls a chunk's derived
status to learn a hub node's terminal outcome. This Protocol is the seam;
the httpx adapter under ``internal/`` is the reference binding, and loop tests
inject a fake or an ``httpx.MockTransport``-backed client — no live hub needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from blizzard.wire.chunk import ChunkDetail, HubAdvanceResponse
from blizzard.wire.completion import CompletionSubmission
from blizzard.wire.decision import DecisionSubmission
from blizzard.wire.envelope import ApplyResponse, NodeEnvelope
from blizzard.wire.facts import RunnerFactAck, RunnerFactBatch
from blizzard.wire.question import QuestionView
from blizzard.wire.queue import QueuePeekResponse
from blizzard.wire.route import RouteClaim, RouteClaimConflict, RouteClaimPausedDenial, RouteClaimResponse


class HubClientError(RuntimeError):
    """A hub call failed at the transport level (unreachable, 5xx, malformed body).

    A 409 route conflict and a 403 paused-runner denial are **not** errors — they
    are expected, distinguishable claim outcomes returned as
    :class:`RouteClaimOutcome`. This type is only raised for genuine transport
    failures the loop treats as "hub unreachable, try next tick".
    """


class ChunkNotFoundError(HubClientError):
    """The hub reports a chunk unknown (404) — terminal, not transient (blizzard#9).

    Raised only by :meth:`IHubClient.get_chunk` / :meth:`IHubClient.get_envelope`,
    the two chunk-identified GET reads: a 404 there means the chunk no longer exists
    at the hub (e.g. after a store reset), which is a different outcome from every
    other :class:`HubClientError` cause (unreachable, 5xx, malformed body) — those
    stay retryable, "hub unreachable, try next tick". A reconcile step that reads a
    chunk it holds treats this subtype as the chunk's tenure having ended out from
    under it: reap any live worker and release the held environments rather than
    retrying the read forever. Still an instance of :class:`HubClientError`, so a
    caller with no special handling for it degrades to the existing retry behavior.
    """


@dataclass(frozen=True)
class RouteClaimOutcome:
    """The result of a route claim: exactly one of ``claimed`` / ``conflict`` /
    ``denied_paused`` set. ``denied_paused`` (issue #44) is distinguished from
    ``conflict`` in the loop: a conflict is a race this claim lost, a paused denial
    means the hub refused it before any race — this runner's own pause brake,
    read authoritatively at the hub rather than off this runner's last-mirrored copy."""

    claimed: RouteClaimResponse | None = None
    conflict: RouteClaimConflict | None = None
    denied_paused: RouteClaimPausedDenial | None = None

    @property
    def won(self) -> bool:
        return self.claimed is not None


class IHubClient(Protocol):
    """The runner's client of the hub API. Outbound-only."""

    def peek_queue(self) -> QueuePeekResponse:
        """``GET /api/queue/peek`` — the hub-ordered ready queue."""
        ...

    def claim_route(self, claim: RouteClaim) -> RouteClaimOutcome:
        """``POST /api/routes`` — claim work; 409 loses the race, 403 means the hub
        registry already has this runner paused (issue #44)."""
        ...

    def submit_completion(self, chunk_id: str, submission: CompletionSubmission) -> ApplyResponse:
        """``POST /api/chunks/{id}/completions`` — the atomic, epoch-fenced write."""
        ...

    def submit_decision(self, chunk_id: str, submission: DecisionSubmission) -> ApplyResponse:
        """``POST /api/chunks/{id}/decisions`` — a runner-config gate parks the chunk."""
        ...

    def push_facts(self, batch: RunnerFactBatch) -> RunnerFactAck:
        """``POST /api/events`` — store-and-forward fact push, seq-idempotent."""
        ...

    def get_envelope(self, chunk_id: str) -> NodeEnvelope:
        """``GET /api/chunks/{id}/envelope`` — the idempotent envelope re-read."""
        ...

    def get_chunk(self, chunk_id: str) -> ChunkDetail:
        """``GET /api/chunks/{id}`` — the chunk's derived status, polled at a hub node."""
        ...

    def hub_advance(self, chunk_id: str) -> HubAdvanceResponse:
        """``POST /api/chunks/{id}/hub-advance`` — drive a chunk parked at a generic
        hub command node one step (#65/#66).

        A no-op at the hub (``ran=False``) when the chunk is not currently parked at a
        generic hub command node, when the fleet-wide serialization slot is busy, or
        when a prior ``pending`` outcome's ``poll_interval`` has not yet elapsed —
        every case simply retried on a later :func:`~blizzard.runner.loop.steps.advance`
        tick (:func:`~blizzard.runner.loop.steps._advance_held_chunk`)."""
        ...

    def get_question(self, question_id: str) -> QuestionView:
        """``GET /api/questions/{id}`` — the runner's answer poll.

        A parked chunk's runner polls its forwarded question by id; once ``answered`` is
        true the answer is delivered by resuming the dormant session around it."""
        ...

    def register_runner(self, runner_id: str, workspace_id: str) -> None:
        """``POST /api/runners`` — register into the fleet registry.

        Idempotent upsert: the runner registers on startup and re-registers each pull,
        which refreshes its ``last_seen_at`` — the runner-level liveness heartbeat the
        board's fleet column derives online/offline from. Called before the paused
        read so the runner is registered by the time it reads its state back."""
        ...

    def fetch_runner_paused(self, runner_id: str) -> bool:
        """``GET /api/runners/{id}`` — the runner's declarative pause brake.

        Read on the outbound pull and adhered to by FILL (paused = no new claims;
        in-flight chunks run on). Never a push into the box."""
        ...

    def report_lease(self, chunk_id: str, *, epoch: int, runner_id: str) -> None:
        """``POST /api/chunks/{id}/leases`` — a ``lease.minted`` fact.

        Reported at every node-step spawn so the hub's epoch fence tracks the runner's:
        a chunk that visits a second runner node (review) submits its completion under a
        fresh epoch the hub must already know, and a requeue's mint is what closes an
        escalation by supersession."""
        ...

    def report_escalation(self, chunk_id: str, *, epoch: int, runner_id: str, takeover_command: str) -> None:
        """``POST /api/chunks/{id}/escalations`` — retries exhausted.

        Lands the escalation at the hub so the chunk derives ``needs_human`` fleet-wide,
        carrying the pasteable takeover command."""
        ...
