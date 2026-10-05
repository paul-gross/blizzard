"""The hub-client seam — the runner's outbound edge to the hub HTTP API.

The runner talks to the hub outbound-only. This Protocol is the seam; the httpx adapter
under ``internal/`` is the reference binding, and a test injects a fake.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from blizzard.foundation.roles import domain_model
from blizzard.runner.harness.capability_snapshot import HarnessCapability
from blizzard.runner.node_steps.chunk_state import ChunkState
from blizzard.runner.node_steps.envelope import Envelope
from blizzard.runner.node_steps.submissions import ApplyReply, Completion, GateSubmission
from blizzard.wire.chunk import HubAdvanceResponse


class HubClientError(RuntimeError):
    """A hub call failed at the transport level (unreachable, 5xx, malformed body).

    A 409 route conflict and a 403 paused denial are **not** errors — they are expected
    claim outcomes returned as :class:`RouteClaimOutcome`."""


class ChunkNotFoundError(HubClientError):
    """The hub reports a chunk unknown (404) — terminal, not transient.

    Raised by :meth:`IHubClient.get_envelope` and, at the chunk-view cache layer
    (:mod:`blizzard.runner.hub.chunk_status_cache`, not ``IHubClient`` itself —
    ``IHubClient.chunk_statuses`` never raises it for an unknown id), by
    :meth:`~blizzard.runner.hub.chunk_status_cache.IChunkViews.get`. Still a
    :class:`HubClientError`, so an unaware caller degrades to the retry behavior."""


class ChunkEndedError(HubClientError):
    """The hub refuses a chunk-scoped call because the chunk has ended (409) — terminal, not transient:
    an envelope read with no current runner node, or a rekey of a route on a ``done``/``stopped`` chunk.
    ``detail`` is the hub's refusal text. Still a :class:`HubClientError`, so an unaware caller retries."""

    def __init__(self, message: str, *, detail: str) -> None:
        super().__init__(message)
        self.detail = detail


@domain_model
@dataclass(frozen=True)
class QueueWorkRef:
    """One work pointer a ready chunk carries: its source and the source-native ref."""

    source: str
    ref: str


@domain_model
@dataclass(frozen=True)
class QueueBlock:
    """A ready chunk's blocked marking: its earliest-declared unmet prerequisite, and how many
    prerequisites are unmet."""

    prerequisite_chunk_id: str
    unmet_count: int = 1


@domain_model
@dataclass(frozen=True)
class QueueEntry:
    """One ready chunk, in the hub's queue order."""

    chunk_id: str
    graph_id: str
    position: int
    work_refs: list[QueueWorkRef] = field(default_factory=list)
    blocked: QueueBlock | None = None


@domain_model
@dataclass(frozen=True)
class SubscriptionDeclaration:
    """One provider subscription this runner declares at registration — the key every usage
    fact names it by."""

    slug: str
    name: str
    provider: str


@domain_model
@dataclass(frozen=True)
class HubQuestion:
    """A worker's question as the hub holds it, with its derived answer and delivery state."""

    question_id: str
    chunk_id: str
    runner_id: str
    epoch: int
    question: str
    asked_at: str
    node_id: str | None = None
    session_id: str | None = None
    harness_id: str | None = None
    options: list[str] = field(default_factory=list)
    answered: bool = False
    answer: str | None = None
    answered_by: str | None = None
    answered_at: str | None = None
    delivered: bool = False
    delivered_at: str | None = None


@domain_model
@dataclass(frozen=True)
class ClaimRequest:
    """A complete route the claiming runner asks the hub for: the chunk, this runner, its
    workspace, and the environments it already bound."""

    chunk_id: str
    runner_id: str
    workspace_id: str
    environment_ids: list[str]


@domain_model
@dataclass(frozen=True)
class ClaimedRoute:
    """A won claim — the route, the chunk's first node envelope, and the route's plaintext
    capability token, which the hub returns exactly once."""

    chunk_id: str
    runner_id: str
    workspace_id: str
    environment_ids: list[str]
    envelope: Envelope
    route_token: str


@domain_model
@dataclass(frozen=True)
class ClaimConflict:
    """A lost race: another runner holds the chunk's route."""

    chunk_id: str
    held_by_runner_id: str
    detail: str = "chunk already claimed"


@domain_model
@dataclass(frozen=True)
class TerminalDenial:
    """The chunk stands at a status no claim is legal from — ended for good (``done``,
    ``stopped``) or not ready yet."""

    chunk_id: str
    status: str
    detail: str = "chunk is terminal"


@domain_model
@dataclass(frozen=True)
class DependencyDenial:
    """The chunk stands on a prerequisite that has not reached ``done``."""

    chunk_id: str
    prerequisite_chunk_id: str
    detail: str = "chunk depends on an unmet prerequisite"


@domain_model
@dataclass(frozen=True)
class IncompatibleDenial:
    """This runner's stored capabilities can no longer run every lineage the chunk can reach."""

    chunk_id: str
    incompatible_runner_id: str
    detail: str = "runner capabilities no longer satisfy the chunk's reachable lineage"


@domain_model
@dataclass(frozen=True)
class PausedDenial:
    """The hub refuses this runner itself — paused, retired, or unregistered — and ``detail``
    names which."""

    chunk_id: str
    runner_id: str
    detail: str = "runner is paused at the hub"


@domain_model
@dataclass(frozen=True)
class RouteClaimOutcome:
    """The result of a route claim: exactly one of ``claimed`` / ``conflict`` /
    ``denied_paused`` / ``denied_terminal`` / ``denied_dependency`` / ``denied_incompatible``
    set — construction refuses any other count. A conflict is a race this claim lost; every
    denial means the hub refused it before any race."""

    claimed: ClaimedRoute | None = None
    conflict: ClaimConflict | None = None
    denied_paused: PausedDenial | None = None
    denied_terminal: TerminalDenial | None = None
    denied_dependency: DependencyDenial | None = None
    denied_incompatible: IncompatibleDenial | None = None

    def __post_init__(self) -> None:
        arms = (
            self.claimed,
            self.conflict,
            self.denied_paused,
            self.denied_terminal,
            self.denied_dependency,
            self.denied_incompatible,
        )
        set_count = sum(arm is not None for arm in arms)
        if set_count != 1:
            raise ValueError(f"a route claim outcome sets exactly one arm, not {set_count}")

    @property
    def won(self) -> bool:  # ast-grep-ignore: bzh:property-delegates
        return self.claimed is not None


@domain_model
@dataclass(frozen=True)
class PushedFact:
    """One buffered runner fact as it is pushed: its per-runner ``seq``, its ``noun.verb`` kind,
    and its kind-specific payload."""

    seq: int
    kind: str
    payload: dict[str, Any]


@domain_model
@dataclass(frozen=True)
class FactPushAck:
    """The hub's acknowledgement of one push against its high-water mark: the new mark, and the
    pushed seqs partitioned into applied, already applied, and rejected for a non-idempotency
    reason."""

    high_water: int
    applied: list[int]
    already_applied: list[int]
    rejected: list[int]


@domain_model
@dataclass(frozen=True)
class TranscriptPush:
    """One buffered transcript record as it is pushed: its lane ``seq`` and its body — the
    record's every field but ``seq``, in the canonical turn format the pump rendered."""

    seq: int
    body: dict[str, Any]


@domain_model
@dataclass(frozen=True)
class TranscriptPushAck:
    """The hub's acknowledgement of one transcript push against the lane's high-water mark.
    ``capped`` records are acknowledged with their content dropped; ``refused`` ones, whose
    lease epoch another holder owns, are acknowledged and never stored."""

    high_water: int
    applied: list[int] = field(default_factory=list)
    already_applied: list[int] = field(default_factory=list)
    capped: list[int] = field(default_factory=list)
    refused: list[int] = field(default_factory=list)


class IChunkStatusReader(Protocol):
    """One method of :class:`IHubClient`'s thirteen (the seam-size ceiling: a new consumer
    re-types to the capability it calls, not the whole wide client). ``IHubClient``
    composes this rather than re-declaring the method — one contract, not two copies free
    to drift."""

    def chunk_statuses(self, chunk_ids: Iterable[str]) -> dict[str, ChunkState]:
        """``GET /api/fleet/chunk-statuses`` (repeatable ``chunk_id``) — every requested id
        present in the store, keyed by ``chunk_id``; an id the hub doesn't know is simply
        absent, never an error. A transport/5xx failure raises ``HubClientError`` for the
        whole call."""
        ...


class IHubClient(IChunkStatusReader, Protocol):
    """The runner's client of the hub API. Outbound-only."""

    def peek_queue(self, capabilities: Sequence[HarnessCapability], *, policy: str) -> list[QueueEntry]:
        """The FILL read — at most one matched entry while this runner holds a token
        (``POST /api/fleet/queue/peek``); the reference binding falls back to the legacy,
        unfiltered ``GET`` on a ``401``, so every caller here sees one uniform call
        regardless of which verb actually served it."""
        ...

    def claim_route(self, claim: ClaimRequest) -> RouteClaimOutcome:
        """``POST /api/fleet/routes`` — claim work; 409 loses the race (or, distinctly,
        the chunk is already terminal or stands on an unmet prerequisite), 403 means
        the hub registry already has this runner paused."""
        ...

    def submit_completion(self, chunk_id: str, completion: Completion) -> ApplyReply:
        """``POST /api/fleet/chunks/{id}/completions`` — the atomic, epoch-fenced write."""
        ...

    def submit_decision(self, chunk_id: str, gate: GateSubmission) -> ApplyReply:
        """``POST /api/fleet/chunks/{id}/decisions`` — a runner-config gate parks the chunk."""
        ...

    def push_facts(self, runner_id: str, facts: Sequence[PushedFact]) -> FactPushAck:
        """``POST /api/fleet/events`` — store-and-forward fact push, seq-idempotent."""
        ...

    def push_transcripts(self, runner_id: str, records: Sequence[TranscriptPush]) -> TranscriptPushAck:
        """``POST /api/fleet/transcripts`` — the transcript lane's own store-and-forward
        push, seq-idempotent against its own high-water mark. Structurally independent
        of :meth:`push_facts`: a wedged or slow
        transcript flush never blocks it."""
        ...

    def get_envelope(self, chunk_id: str) -> Envelope:
        """``GET /api/fleet/chunks/{id}/envelope`` — the idempotent envelope re-read. Raises
        :class:`ChunkNotFoundError` for an unknown chunk and :class:`ChunkEndedError` for one
        that has ended."""
        ...

    def hub_advance(self, chunk_id: str) -> HubAdvanceResponse:
        """``POST /api/fleet/chunks/{id}/hub-advance`` — drive a chunk parked at a generic
        hub command node one step (#65/#66).

        ``ran=False`` means the hub declined to run a step this call — simply retried on a
        later :class:`~blizzard.runner.loop.steps.Advance` tick."""
        ...

    def get_question(self, question_id: str) -> HubQuestion:
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
        capabilities: tuple[HarnessCapability, ...] = (),
        subscriptions: tuple[SubscriptionDeclaration, ...] = (),
        gates: tuple[str, ...] = (),
    ) -> None:
        """``POST /api/fleet/runners`` — register into the fleet registry. Idempotent
        upsert and the liveness heartbeat, called before the paused read. Every optional
        field, ``subscriptions`` included, is unconditionally overwritten each call;
        ``subscriptions`` is always a list, never omitted. ``gates`` is the runner's own configured
        human-gate node names — reported for display, never read back to enforce."""
        ...

    def fetch_runner_paused(self, runner_id: str) -> bool:
        """``GET /api/fleet/runners/{id}`` — the runner's declarative pause brake.

        Read on the outbound pull; never a push into the box."""
        ...

    def rekey_route_token(self, chunk_id: str) -> str:
        """``POST /api/fleet/chunks/{id}/route-token`` — rotate the chunk's route
        capability token. Why it exists: `src/blizzard/hub/domain/execution/claim.py`'s
        ``ClaimService.rekey``. Raises :class:`ChunkEndedError` when the live route sits on an
        ended chunk."""
        ...
