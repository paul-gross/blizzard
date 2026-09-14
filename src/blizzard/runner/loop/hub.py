"""The hub-client seam — the runner's outbound edge to the hub HTTP API.

The runner talks to the hub outbound-only. This Protocol is the seam; the httpx adapter
under ``internal/`` is the reference binding, and a test injects a fake.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from blizzard.wire.chunk import ChunkStatusView, HubAdvanceResponse
from blizzard.wire.completion import CompletionSubmission
from blizzard.wire.decision import DecisionSubmission
from blizzard.wire.envelope import ApplyResponse, NodeEnvelope
from blizzard.wire.facts import RunnerFactAck, RunnerFactBatch
from blizzard.wire.question import QuestionView
from blizzard.wire.queue import QueuePeekResponse
from blizzard.wire.route import (
    RouteClaim,
    RouteClaimConflict,
    RouteClaimDependencyDenial,
    RouteClaimPausedDenial,
    RouteClaimResponse,
    RouteClaimTerminalDenial,
    RouteTokenRekeyResponse,
)
from blizzard.wire.transcript_segment import TranscriptSegmentAck, TranscriptSegmentBatch


class HubClientError(RuntimeError):
    """A hub call failed at the transport level (unreachable, 5xx, malformed body).

    A 409 route conflict and a 403 paused denial are **not** errors — they are expected
    claim outcomes returned as :class:`RouteClaimOutcome`."""


class ChunkNotFoundError(HubClientError):
    """The hub reports a chunk unknown (404) — terminal, not transient (blizzard#9).

    Raised by :meth:`IHubClient.get_envelope` and, at the chunk-view cache layer
    (:mod:`blizzard.runner.loop.chunk_status_cache`, not ``IHubClient`` itself —
    ``IHubClient.chunk_statuses`` never raises it for an unknown id), by
    :meth:`~blizzard.runner.loop.chunk_status_cache.IChunkViews.get`. Still a
    :class:`HubClientError`, so an unaware caller degrades to the retry behavior."""


@dataclass(frozen=True)
class RouteClaimOutcome:
    """The result of a route claim: exactly one of ``claimed`` / ``conflict`` /
    ``denied_paused`` (#44) / ``denied_terminal`` (#118) / ``denied_dependency``
    (blizzard#458) set. A conflict is a race this claim lost; every denial means the hub
    refused it before any race."""

    claimed: RouteClaimResponse | None = None
    conflict: RouteClaimConflict | None = None
    denied_paused: RouteClaimPausedDenial | None = None
    denied_terminal: RouteClaimTerminalDenial | None = None
    denied_dependency: RouteClaimDependencyDenial | None = None

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
        the chunk is already terminal — issue #118 — or stands on an unmet prerequisite —
        blizzard#458), 403 means the hub registry already has this runner paused (issue
        #44)."""
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

    def push_transcripts(self, batch: TranscriptSegmentBatch) -> TranscriptSegmentAck:
        """``POST /api/fleet/transcripts`` — the transcript lane's own store-and-forward
        push, seq-idempotent against its own high-water mark (D3, issue #246; hub storage
        is blizzard#247). Structurally independent of :meth:`push_facts`: a wedged or slow
        transcript flush never blocks it."""
        ...

    def get_envelope(self, chunk_id: str) -> NodeEnvelope:
        """``GET /api/fleet/chunks/{id}/envelope`` — the idempotent envelope re-read."""
        ...

    def chunk_statuses(self, chunk_ids: Iterable[str]) -> dict[str, ChunkStatusView]:
        """``GET /api/fleet/chunk-statuses`` (repeatable ``chunk_id``) — every requested id
        present in the store, keyed by ``chunk_id``; an id the hub doesn't know is simply
        absent, never an error. A transport/5xx failure raises ``HubClientError`` for the
        whole call."""
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

    def rekey_route_token(self, chunk_id: str) -> RouteTokenRekeyResponse:
        """``POST /api/fleet/chunks/{id}/route-token`` — rotate the chunk's route
        capability token (issue #84b). Why it exists: `src/blizzard/hub/domain/claim.py`'s
        ``ClaimService.rekey``."""
        ...


class IChunkStatusReader(Protocol):
    """The narrow seam :mod:`blizzard.runner.loop.chunk_status_cache`'s two ``IChunkViews``
    bindings actually call — one method of :class:`IHubClient`'s thirteen (the seam-size
    ceiling: a new consumer re-types to the capability it calls, not the whole wide client).
    ``HttpHubClient``/``FakeHub`` satisfy this structurally, with no changes of their own."""

    def chunk_statuses(self, chunk_ids: Iterable[str]) -> dict[str, ChunkStatusView]:
        """``GET /api/fleet/chunk-statuses`` (repeatable ``chunk_id``) — every requested id
        present in the store, keyed by ``chunk_id``; an id the hub doesn't know is simply
        absent, never an error. A transport/5xx failure raises ``HubClientError`` for the
        whole call."""
        ...
