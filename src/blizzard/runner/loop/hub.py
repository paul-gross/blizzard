"""The hub-client seam — the runner's outbound edge to the hub HTTP API.

The runner talks to the hub outbound-only. This Protocol is the seam; the httpx adapter
under ``internal/`` is the reference binding, and a test injects a fake.
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
from blizzard.wire.route import (
    RouteClaim,
    RouteClaimConflict,
    RouteClaimPausedDenial,
    RouteClaimResponse,
    RouteClaimTerminalDenial,
    RouteTokenRekeyResponse,
)


class HubClientError(RuntimeError):
    """A hub call failed at the transport level (unreachable, 5xx, malformed body).

    A 409 route conflict and a 403 paused denial are **not** errors — they are expected
    claim outcomes returned as :class:`RouteClaimOutcome`."""


class ChunkNotFoundError(HubClientError):
    """The hub reports a chunk unknown (404) — terminal, not transient (blizzard#9).

    Raised only by the two chunk-identified GET reads. Still a
    :class:`HubClientError`, so an unaware caller degrades to the retry behavior."""


@dataclass(frozen=True)
class RouteClaimOutcome:
    """The result of a route claim: exactly one of ``claimed`` / ``conflict`` /
    ``denied_paused`` (#44) / ``denied_terminal`` (#118) set. A conflict is a race this
    claim lost; either denial means the hub refused it before any race."""

    claimed: RouteClaimResponse | None = None
    conflict: RouteClaimConflict | None = None
    denied_paused: RouteClaimPausedDenial | None = None
    denied_terminal: RouteClaimTerminalDenial | None = None

    @property
    def won(self) -> bool:
        return self.claimed is not None


class IHubClient(Protocol):
    """The runner's client of the hub API. Outbound-only."""

    def peek_queue(self) -> QueuePeekResponse:
        """``GET /api/fleet/queue/peek`` — the hub-ordered ready queue."""
        ...

    def claim_route(self, claim: RouteClaim) -> RouteClaimOutcome:
        """``POST /api/fleet/routes`` — claim work; 409 loses the race (or, distinctly,
        the chunk is already terminal — issue #118), 403 means the hub registry already
        has this runner paused (issue #44)."""
        ...

    def submit_completion(self, chunk_id: str, submission: CompletionSubmission) -> ApplyResponse:
        """``POST /api/fleet/chunks/{id}/completions`` — the atomic, epoch-fenced write."""
        ...

    def submit_decision(self, chunk_id: str, submission: DecisionSubmission) -> ApplyResponse:
        """``POST /api/fleet/chunks/{id}/decisions`` — a runner-config gate parks the chunk."""
        ...

    def push_facts(self, batch: RunnerFactBatch) -> RunnerFactAck:
        """``POST /api/fleet/events`` — store-and-forward fact push, seq-idempotent."""
        ...

    def get_envelope(self, chunk_id: str) -> NodeEnvelope:
        """``GET /api/fleet/chunks/{id}/envelope`` — the idempotent envelope re-read."""
        ...

    def get_chunk(self, chunk_id: str) -> ChunkDetail:
        """``GET /api/fleet/chunks/{id}`` — the chunk's derived status, polled at a hub node."""
        ...

    def hub_advance(self, chunk_id: str) -> HubAdvanceResponse:
        """``POST /api/fleet/chunks/{id}/hub-advance`` — drive a chunk parked at a generic
        hub command node one step (#65/#66).

        ``ran=False`` means the hub declined to run a step this call — simply retried on a
        later :class:`~blizzard.runner.loop.steps.Advance` tick."""
        ...

    def get_question(self, question_id: str) -> QuestionView:
        """``GET /api/fleet/questions/{id}`` — the runner's answer poll, by question id."""
        ...

    def register_runner(
        self,
        runner_id: str,
        workspace_id: str,
        *,
        env_capacity: int | None = None,
        url: str | None = None,
        redirect_uris: tuple[str, ...] = (),
    ) -> None:
        """``POST /api/fleet/runners`` — register into the fleet registry.

        Idempotent upsert, and the runner-level liveness heartbeat. Called before the
        paused read so the runner is registered by the time it reads its state back.
        Every optional field is an unconditional overwrite on each (re-)registration."""
        ...

    def fetch_runner_paused(self, runner_id: str) -> bool:
        """``GET /api/fleet/runners/{id}`` — the runner's declarative pause brake.

        Read on the outbound pull; never a push into the box."""
        ...

    def report_lease(self, chunk_id: str, *, epoch: int, runner_id: str) -> None:
        """``POST /api/fleet/chunks/{id}/leases`` — a ``lease.minted`` fact.

        Reported at every node-step spawn so the hub's epoch fence tracks the runner's."""
        ...

    def report_escalation(
        self, chunk_id: str, *, epoch: int, runner_id: str, takeover_command: str, wrapped_takeover_command: str = ""
    ) -> None:
        """``POST /api/fleet/chunks/{id}/escalations`` — retries exhausted.

        Lands the escalation at the hub so the chunk derives ``needs_human`` fleet-wide,
        carrying the pasteable takeover command and its wrapped equivalent."""
        ...

    def rekey_route_token(self, chunk_id: str) -> RouteTokenRekeyResponse:
        """``POST /api/fleet/chunks/{id}/route-token`` — rotate the chunk's route
        capability token (issue #84b). Why it exists: `src/blizzard/hub/domain/claim.py`'s
        ``ClaimService.rekey``."""
        ...
