"""Work-lifecycle domain — the chunk, its facts, and its **derived** status.

The center of the model (D-024). Per ``bzh:facts-not-status`` a chunk's status is
never a stored column: it is computed here, by :func:`derive_chunk_status`, from
the recorded facts (D-004/D-067). This module owns the derivation queries the hub
runs — expressed as pure functions over already-loaded domain facts
(``bzh:domain-takes-objects``), so they unit-test with zero store and zero tokens.

The fact inputs (:class:`ChunkFacts`) are the domain-object form of the hub-store
rows in :mod:`blizzard.hub.store.schema`; a read repository hydrates them (the
:class:`IReadChunkRepository` seam), and the domain derives from the objects.

Precedence is **first match wins**, top to bottom, exactly as
``design/domain/events.md`` specifies — a chunk matching several rows has exactly
one status. ``waiting_on_human`` (open ask / open decision) sits between
``needs_human`` and ``delivering`` in the full design; ask/answer and gates are
P7 (see ORCHESTRATION.md), so its branch is a shaped extension point here, not yet
wired — the fact inputs for it are absent from :class:`ChunkFacts` until then.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from blizzard.hub.domain.artifacts import ArtifactRow
from blizzard.hub.domain.fleet import Route
from blizzard.hub.domain.graph import Executor


class ChunkStatus(StrEnum):
    """The derived chunk statuses (D-067). Never stored — always a query result."""

    READY = "ready"
    RUNNING = "running"
    DELIVERING = "delivering"
    WAITING_ON_HUMAN = "waiting_on_human"
    NEEDS_HUMAN = "needs_human"
    STOPPED = "stopped"
    DONE = "done"


# --- Domain objects ---------------------------------------------------------


@dataclass(frozen=True)
class PmPointer:
    """One wrapped PM item — ``{provider, url}`` (D-075). Contents never stored."""

    provider: str
    url: str


@dataclass(frozen=True)
class Chunk:
    """The unit of work that travels the workflow graph (D-024/D-047)."""

    chunk_id: str
    graph_id: str
    pm_pointers: list[PmPointer]
    minted_at: datetime


# --- Facts that feed the derivations ---------------------------------------
#
# Each is the domain-object form of a fact row. Only the fields the derivations
# read are carried; a hydrating repository fills them.


@dataclass(frozen=True)
class RouteCreatedFact:
    """A ``route.created`` fact — the claim (D-021/D-080)."""

    created_at: datetime


@dataclass(frozen=True)
class RouteReleasedFact:
    """A ``route.released`` fact — forcible detach (D-088)."""

    released_at: datetime


@dataclass(frozen=True)
class LeaseFact:
    """A ``lease.minted`` fact reported up from a runner (D-044)."""

    epoch: int
    minted_at: datetime


@dataclass(frozen=True)
class TransitionFact:
    """A ``transition.recorded`` fact (D-027) with its target node's executor.

    ``to_node_executor`` is resolved by the hydrating repository (a join to the
    pinned graph's nodes) so the derivation stays a pure function — the domain
    never re-opens a store to learn whether the target is a hub node.
    """

    to_node_id: str
    to_node_executor: Executor
    epoch: int
    recorded_at: datetime


@dataclass(frozen=True)
class EscalationFact:
    """An ``escalation.recorded`` fact — retries exhausted / dead worker (D-009).

    Carries the runner-composed **takeover command** (D-035/harness-adapters.md): the
    literal ``cd <workdir> && <harness resume>`` a human pastes to enter the parked
    session. It is surfaced on the chunk detail so ``needs_human`` is actionable; the
    status derivation itself keys only on ``(epoch, recorded_at)`` supersession (D-067).
    """

    epoch: int
    recorded_at: datetime
    takeover_command: str = ""


@dataclass(frozen=True)
class ChunkFacts:
    """Every fact a chunk's status derives from (D-067), already loaded.

    The derivation is a pure function of this aggregate — the unit tests build it
    directly, no store required.
    """

    minted: bool
    stopped: bool = False
    delivery_landed: bool = False
    escalations: list[EscalationFact] = field(default_factory=list)
    leases: list[LeaseFact] = field(default_factory=list)
    transitions: list[TransitionFact] = field(default_factory=list)
    routes_created: list[RouteCreatedFact] = field(default_factory=list)
    routes_released: list[RouteReleasedFact] = field(default_factory=list)


# --- The derivation queries (D-067) -----------------------------------------


def derive_chunk_status(facts: ChunkFacts) -> ChunkStatus:
    """Derive a chunk's single status from its facts, first match wins (D-067)."""
    if facts.stopped:
        return ChunkStatus.STOPPED
    if facts.delivery_landed:
        return ChunkStatus.DONE
    if _has_open_escalation(facts):
        return ChunkStatus.NEEDS_HUMAN
    # waiting_on_human (open ask / open decision) slots here in the full design —
    # ask/answer and gates are P7; no fact inputs for it exist yet.
    if _newest_transition_enters_hub_node(facts):
        return ChunkStatus.DELIVERING
    if _has_live_route(facts):
        return ChunkStatus.RUNNING
    return ChunkStatus.READY


def _has_open_escalation(facts: ChunkFacts) -> bool:
    """An escalation with no later lease mint — supersession, not resolution (D-067)."""
    return open_escalation(facts) is not None


def open_escalation(facts: ChunkFacts) -> EscalationFact | None:
    """The newest escalation not yet closed by a later lease mint (D-067), or ``None``.

    Requeue/takeover close an escalation by **supersession** — the next lease mint,
    not a resolution fact — so an escalation stays open exactly while no lease was
    minted after it. When open, its ``takeover_command`` is the resume command a human
    pastes (harness-adapters.md); the board surfaces it on the ``needs_human`` chunk.
    """
    if not facts.escalations:
        return None
    newest = max(facts.escalations, key=lambda e: e.recorded_at)
    if any(lease.minted_at > newest.recorded_at for lease in facts.leases):
        return None
    return newest


def _newest_transition_enters_hub_node(facts: ChunkFacts) -> bool:
    """The newest accepted transition's target is a hub-executed node (D-030)."""
    transition = newest_transition(facts)
    return transition is not None and transition.to_node_executor is Executor.HUB


def _has_live_route(facts: ChunkFacts) -> bool:
    """A ``route.created`` with no later ``route.released`` (D-088)."""
    if not facts.routes_created:
        return False
    newest_created = max(facts.routes_created, key=lambda r: r.created_at)
    return not any(rel.released_at > newest_created.created_at for rel in facts.routes_released)


def newest_transition(facts: ChunkFacts) -> TransitionFact | None:
    """The chunk's newest accepted transition — its current node derives from this.

    Ordered by ``(recorded_at, epoch)``: the timestamp is the primary key, and the
    fencing epoch (monotonic per chunk) breaks a tie between two transitions stamped
    at the same instant — a coordinator that authors a follow-on transition under a
    higher epoch in the same tick, or any virtual-clock test.
    """
    if not facts.transitions:
        return None
    return max(facts.transitions, key=lambda t: (t.recorded_at, t.epoch))


def current_node_id(facts: ChunkFacts) -> str | None:
    """The chunk's current node id — the newest transition's target, else ``None``.

    ``None`` means the chunk has not yet transitioned, so its current node is the
    pinned graph's entry node — a graph the caller already holds; resolving it is
    not this function's job (``bzh:domain-takes-objects``).
    """
    transition = newest_transition(facts)
    return transition.to_node_id if transition is not None else None


def latest_epoch(facts: ChunkFacts) -> int | None:
    """The chunk's latest fencing epoch — its newest lease's, else ``None`` (D-007)."""
    if not facts.leases:
        return None
    return max(lease.epoch for lease in facts.leases)


# --- Repository seams (I-prefix, read/write split — bzh:repository-split) ----


class IReadChunkRepository(Protocol):
    """Read-only chunk access. Controllers at the edges depend on this variant."""

    def get(self, chunk_id: str) -> Chunk | None: ...
    def load_facts(self, chunk_id: str) -> ChunkFacts | None: ...
    def load_artifacts(self, chunk_id: str) -> list[ArtifactRow]:
        """Every artifact row of a chunk; the caller resolves latest-by-epoch (D-089)."""
        ...

    def route_of(self, chunk_id: str) -> Route | None:
        """The chunk's live route (runner/workspace/envs), or None if unclaimed/released."""
        ...

    def list_ready(self) -> list[Chunk]: ...
    def list_all(self) -> list[Chunk]: ...
    def find_live_holder(self, pointer: PmPointer) -> str | None:
        """The chunk_id of a live (non-terminal) chunk holding ``pointer`` (D-093), or None."""
        ...

    def accepted_transition_target(self, chunk_id: str, *, from_node_id: str, epoch: int) -> str | None:
        """The ``to_node_id`` of an already-accepted transition out of ``from_node_id`` at
        ``epoch`` — the idempotency probe for a re-applied completion (D-090), or None."""
        ...

    def landed_repos(self, chunk_id: str) -> set[str]:
        """The repos already landed for a chunk — the delivery reconciliation skip-set (D-091)."""
        ...


class IWriteChunkRepository(IReadChunkRepository, Protocol):
    """Read-write chunk access. Only the domain layer depends on this variant."""

    def mint(self, chunk: Chunk) -> None: ...
    def record_lease(self, chunk_id: str, *, epoch: int, runner_id: str, at: datetime) -> None: ...
    def record_route(self, route: Route, *, at: datetime) -> None: ...
    def record_route_released(self, chunk_id: str, *, at: datetime) -> None: ...
    def record_transition(
        self,
        *,
        transition_id: str,
        chunk_id: str,
        from_node_id: str | None,
        to_node_id: str,
        choice_name: str | None,
        epoch: int,
        runner_id: str,
        at: datetime,
        artifacts: list[ArtifactRow],
    ) -> None:
        """One node-step's transition and its artifacts, written atomically (D-036)."""
        ...

    def record_delivery_repo_landed(self, chunk_id: str, *, repo: str, commit_hash: str, at: datetime) -> None: ...
    def record_delivery_landed(self, chunk_id: str, *, at: datetime) -> None: ...
    def record_escalation(self, chunk_id: str, *, epoch: int, takeover_command: str, at: datetime) -> None:
        """Record an ``escalation.recorded`` fact reported up by a runner (D-009/D-067).

        Retries exhausted (or a dead worker past the cap): the chunk derives
        ``needs_human`` until a later lease mint supersedes it. The runner-composed
        takeover command rides along so the parked session is resumable (D-035)."""
        ...
