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
from blizzard.wire.envelope import ApplyResponse, NodeEnvelope
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

    def get_envelope(self, chunk_id: str) -> NodeEnvelope:
        """``GET /api/chunks/{id}/envelope`` — the idempotent envelope re-read (D-090)."""
        ...

    def get_chunk(self, chunk_id: str) -> ChunkDetail:
        """``GET /api/chunks/{id}`` — the chunk's derived status, polled at a hub node (D-066)."""
        ...
