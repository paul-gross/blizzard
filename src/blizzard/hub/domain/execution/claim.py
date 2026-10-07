"""Route claim — how a runner takes work.

The ``POST /routes`` domain rule: the hub accepts **exactly one** claim per chunk, and
the winning claim's result carries the chunk's first node envelope. A runner marked
``hub_paused`` is refused before the race is run, and only for new claims.
The load-facts → check-live-route → record-route sequence is an atomic CAS."""

from __future__ import annotations

import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.foundation.roles import domain_model
from blizzard.foundation.tokens import TokenHash
from blizzard.hub.domain.chunk.model import (
    Chunk,
    ChunkFacts,
    ChunkVerb,
    DependencyEdge,
    WorkRefLabel,
    holds_claim,
    verb_legal_from,
)
from blizzard.hub.domain.chunk.ports.artifacts import IReadChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.exclusive import IChunkExclusiveWrites, ILockedChunkRead
from blizzard.hub.domain.chunk.ports.route import IWriteChunkRouteRepository
from blizzard.hub.domain.execution.eligibility import EligibilityCheck
from blizzard.hub.domain.execution.envelope import Envelope
from blizzard.hub.domain.graph.model import Graph, IReadGraphRepository, Node
from blizzard.hub.domain.runners.registration import IReadRunnerRegistry, RetiredRunnerGuard, RunnerRegistration
from blizzard.hub.domain.runners.route import Route

#: `secrets.token_urlsafe` byte count for the route capability token (43 URL-safe chars).
_ROUTE_TOKEN_BYTES = 32

# Crash point (``bzh:crash-point-registry``): route durable, plaintext not yet delivered.
_CP_CLAIM_AFTER_PERSIST_BEFORE_RESPONSE = crashpoint(
    "claim.after-persist.before-response",
    "the route + its route_token_minted fact are durable; the plaintext has not yet reached the runner",
)


class ClaimConflict(Exception):
    """The chunk already has a live route — this claim lost the race."""

    def __init__(self, *, held_by_runner_id: str) -> None:
        super().__init__(f"chunk already claimed by runner {held_by_runner_id}")
        self.held_by_runner_id = held_by_runner_id


class ClaimDeniedPaused(Exception):
    """The claiming runner is paused at the hub registry — refused before any race.

    Distinct from :class:`ClaimConflict`: this runner did not lose to another claimant,
    it was never eligible to claim in the first place."""

    def __init__(self, *, runner_id: str) -> None:
        super().__init__(f"runner {runner_id} is paused at the hub")
        self.runner_id = runner_id


class ClaimDeniedUnregistered(Exception):
    """The claiming runner has never registered at the hub — added but never connected — refused
    before any race, in the runner-refusal shape of :class:`ClaimDeniedPaused`. Neither runner brake
    nor its capabilities can be judged without a registration, and a live runner re-registers every
    tick."""

    def __init__(self, *, runner_id: str) -> None:
        super().__init__(f"runner {runner_id} is not registered at the hub")
        self.runner_id = runner_id


class ClaimDeniedTerminal(Exception):
    """The chunk is already terminal ({done, stopped}) — refused before the race,
    mirroring :class:`ClaimDeniedPaused`'s shape: this is not a race loss, the chunk
    can never be claimed again. Status is re-derived fresh under the claim lock."""

    def __init__(self, *, chunk_id: str, status: ChunkStatus) -> None:
        super().__init__(f"chunk {chunk_id} is {status.value}, not claimable")
        self.chunk_id = chunk_id
        self.status = status


class ClaimDeniedDependency(Exception):
    """The chunk stands on a prerequisite not yet ``done``, mirroring
    :class:`ClaimDeniedTerminal`'s shape and re-derived under the claim lock. Names the one
    unmet edge found, earliest-declared first."""

    def __init__(self, *, chunk_id: str, prerequisite_chunk_id: str) -> None:
        super().__init__(f"chunk {chunk_id} depends on unmet prerequisite {prerequisite_chunk_id}")
        self.chunk_id = chunk_id
        self.prerequisite_chunk_id = prerequisite_chunk_id


class ClaimDeniedIncompatible(Exception):
    """The claiming runner's stored capabilities can no longer run every statically
    reachable runner-owned lineage from the chunk's current node — refused outright,
    mirroring :class:`ClaimDeniedDependency`'s shape. A registration
    reporting no capabilities is refused here too."""

    def __init__(self, *, chunk_id: str, runner_id: str) -> None:
        super().__init__(f"runner {runner_id}'s capabilities no longer satisfy chunk {chunk_id}")
        self.chunk_id = chunk_id
        self.runner_id = runner_id


class ClaimDeniedNotReady(Exception):
    """The chunk is not ``ready`` — never promoted, paused while unclaimed, or parked on a human
    with no live route — so the hub grants it to no runner. Distinct from
    :class:`ClaimDeniedPaused`, which refuses the *runner*, and from :class:`ClaimConflict`,
    which a held route answers first."""

    def __init__(self, *, chunk_id: str, status: ChunkStatus) -> None:
        super().__init__(f"chunk {chunk_id} is {status.value}, not ready to claim")
        self.chunk_id = chunk_id
        self.status = status


class RekeyDeniedTerminal(Exception):
    """The live route sits on an ended chunk, where it confers no tenure — no token is minted."""

    def __init__(self, *, chunk_id: str, status: ChunkStatus) -> None:
        super().__init__(f"chunk {chunk_id} is {status.value}, its route confers no tenure")
        self.chunk_id = chunk_id
        self.status = status


def refuse_paused_runner(registration: RunnerRegistration | None, *, runner_id: str) -> RunnerRegistration:
    """Refuse a claim from a runner unregistered (never connected included), retired, or paused at
    the hub registry, ahead of any race; the registration the claim stands on otherwise."""
    if registration is None or registration.never_connected():
        raise ClaimDeniedUnregistered(runner_id=runner_id)
    registration.refuse_if_retired(action="claim")
    if registration.hub_paused:
        raise ClaimDeniedPaused(runner_id=runner_id)
    return registration


def refuse_rekey(route: Route, facts: ChunkFacts) -> None:
    """Refuse rotating ``route``'s token once its chunk has ended (:class:`RekeyDeniedTerminal`):
    a route left on a stopped or done chunk confers no tenure, so no token is minted for it."""
    status = facts.status()
    if not verb_legal_from(ChunkVerb.REKEY_ROUTE_TOKEN, status):
        raise RekeyDeniedTerminal(chunk_id=route.chunk_id, status=status)


def first_unmet_prerequisite(
    chunk_id: str, edges: Sequence[DependencyEdge], prerequisite_facts: Mapping[str, ChunkFacts]
) -> str | None:
    """The earliest-declared standing edge naming ``chunk_id`` as dependent whose prerequisite does
    not meet it (:meth:`DependencyEdge.met_by`) — ``None`` when every such edge is met. No
    dependent-status filter: claim judges its own chunk whatever it derives."""
    for edge in edges:
        if edge.dependent_chunk_id != chunk_id:
            continue
        facts = prerequisite_facts.get(edge.prerequisite_chunk_id)
        if not edge.met_by(facts.status() if facts is not None else None):
            return edge.prerequisite_chunk_id
    return None


@domain_model
@dataclass(frozen=True)
class ClaimAdmission:
    """Whether a runner may claim a chunk, judged on what the claim lock read. Refusals run in a fixed order,
    each its own error: unregistered (never connected included), retired, ended, held route, not ``ready``,
    unmet prerequisite, then incapable runner (a registration reporting no capabilities is incapable of
    everything) — the runner's own standing before any chunk refusal, its fit to the chunk's node last. The
    runner-paused brake is judged before the lock (:func:`refuse_paused_runner`)."""

    chunk: Chunk
    graph: Graph
    facts: ChunkFacts | None

    def node(
        self,
        *,
        runner_id: str,
        existing_route: Route | None,
        unmet_prerequisite: str | None,
        registration: RunnerRegistration | None,
    ) -> Node:
        """The node the claim lands the chunk at, or the refusal. Its envelope is built from it."""
        if registration is None or registration.never_connected():
            raise ClaimDeniedUnregistered(runner_id=runner_id)
        registration.refuse_if_retired(action="claim")
        chunk_id = self.chunk.chunk_id
        facts = ChunkFacts.or_default(self.facts)
        status = facts.status() if self.facts is not None else ChunkStatus.NOT_READY
        if not holds_claim(status):
            raise ClaimDeniedTerminal(chunk_id=chunk_id, status=status)
        if existing_route is not None:
            raise ClaimConflict(held_by_runner_id=existing_route.runner_id)
        if not verb_legal_from(ChunkVerb.CLAIM, status):
            raise ClaimDeniedNotReady(chunk_id=chunk_id, status=status)
        if unmet_prerequisite is not None:
            raise ClaimDeniedDependency(chunk_id=chunk_id, prerequisite_chunk_id=unmet_prerequisite)
        node = facts.current_node(self.graph)
        if node is None:  # pragma: no cover - a pinned graph always resolves its own node
            raise ClaimConflict(held_by_runner_id=runner_id)
        if not EligibilityCheck(self.chunk, self.graph, node, registration.capabilities).eligible:
            raise ClaimDeniedIncompatible(chunk_id=chunk_id, runner_id=runner_id)
        return node


@domain_model
@dataclass(frozen=True)
class ClaimResult:
    """A won claim — the route fact, its first node envelope, and the route's plaintext
    capability token. ``route_token`` is returned exactly once, here; only
    its sha256 hash is persisted. ``route_id`` is the freshly-minted route
    id, which :class:`~blizzard.hub.domain.runners.route.Route` itself does not carry."""

    route: Route
    envelope: Envelope
    route_token: str
    route_id: str


class ClaimService:
    """Claim a chunk for a runner, exactly-one-wins, and paused-runners-need-not-apply."""

    def __init__(
        self,
        *,
        route: IWriteChunkRouteRepository,
        artifacts: IReadChunkArtifactsRepository,
        graphs: IReadGraphRepository,
        registry: IReadRunnerRegistry,
        retired: RetiredRunnerGuard,
        exclusive: IChunkExclusiveWrites,
        clock: IClock,
        label: WorkRefLabel,
    ) -> None:
        self._route = route
        self._artifacts = artifacts
        # Re-resolves the chunk's graph fresh under the lock — see `_claim_locked`.
        self._graphs = graphs
        # The pre-lock paused-runner peek only — every guard read inside the CAS itself
        # goes through the locked handle instead (`_claim_locked`'s own re-fetch).
        self._registry = registry
        # The locked-transaction seam (``bzh:store-exclusive-write``): the route CAS runs in
        # one row-locked write transaction, never an in-process lock.
        self._exclusive = exclusive
        # The rekey's refusal; the claim refuses through the registration it already reads.
        self._retired = retired
        self._clock = clock
        self._label = label

    # runner_id resolves a paused-runner guard, a domain rule (bzh:domain-takes-objects).
    # ast-grep-ignore: bzh:domain-takes-objects
    def claim(
        self,
        chunk: Chunk,
        graph: Graph,
        *,
        runner_id: str,
        workspace_id: str,
        environment_ids: list[str],
    ) -> ClaimResult:
        # Checked before the lock: a paused runner is refused regardless of whether it
        # would have won the race, so there is nothing here for the CAS to serialize.
        refuse_paused_runner(self._registry.get_runner(runner_id), runner_id=runner_id)
        with self._exclusive.locked([chunk.chunk_id]) as handle:
            return self._claim_locked(
                handle, chunk, graph, runner_id=runner_id, workspace_id=workspace_id, environment_ids=environment_ids
            )

    def _claim_locked(
        self,
        handle: ILockedChunkRead,
        chunk: Chunk,
        graph: Graph,
        *,
        runner_id: str,
        workspace_id: str,
        environment_ids: list[str],
    ) -> ClaimResult:
        # Re-read the chunk under the lock: an edit that landed first may have
        # moved `graph_id`/`model` since the edge resolved the handed-in objects.
        current = handle.record(chunk.chunk_id)
        if current is None:  # pragma: no cover - the chunk cannot vanish mid-claim
            raise ClaimConflict(held_by_runner_id=runner_id)
        if current.graph_id != chunk.graph_id:
            fresh_graph = self._graphs.get(current.graph_id)
            if fresh_graph is None:  # pragma: no cover - a pinned graph always resolves
                raise ClaimConflict(held_by_runner_id=runner_id)
            graph = fresh_graph
        chunk = current

        facts = handle.facts(chunk.chunk_id)
        # Every guard input is re-read under the lock — a stop, an edge, a completion, or a
        # capability change can land after the pre-lock peek.
        edges = handle.standing_edges()
        prerequisites = [e.prerequisite_chunk_id for e in edges if e.dependent_chunk_id == chunk.chunk_id]
        ClaimAdmission(chunk, graph, facts).node(
            runner_id=runner_id,
            existing_route=handle.route_of(chunk.chunk_id),
            unmet_prerequisite=first_unmet_prerequisite(chunk.chunk_id, edges, handle.facts_for(prerequisites)),
            registration=handle.runner_registration(runner_id),
        )
        now = self._clock.now()

        route = Route(
            chunk_id=chunk.chunk_id,
            runner_id=runner_id,
            workspace_id=workspace_id,
            environment_ids=list(environment_ids),
            created_at=now,
        )
        # Minted fresh per acquisition: the plaintext is returned once and
        # never stored — only its sha256 hash lands, in the same write as record_route.
        route_token = secrets.token_urlsafe(_ROUTE_TOKEN_BYTES)
        route_id = self._route.record_route_locked(handle, route, token_hash=TokenHash(route_token).hex, at=now)
        _CP_CLAIM_AFTER_PERSIST_BEFORE_RESPONSE.reached()

        # Envelope assembly stays outside the locked transaction's read set — the
        # artifact load is no part of the exactly-one-wins decision itself.
        envelope = Envelope.current(
            chunk,
            graph,
            ChunkFacts.or_default(facts),
            self._artifacts.load_artifacts(chunk.chunk_id),
            label=self._label,
        )
        return ClaimResult(route=route, envelope=envelope, route_token=route_token, route_id=route_id)

    def rekey(self, route: Route, facts: ChunkFacts) -> str:
        """Rotate a live route's capability token — the lost-plaintext recovery: a claim whose route-token
        response was never read back has no other way to learn it. Appends a new ``route_token_minted`` fact
        (``bzh:facts-not-status``), newest-fact-wins, re-run idempotent; :func:`refuse_rekey` refuses a
        route left on an ended chunk."""
        self._retired.refuse_if_retired(route.runner_id, action="route-token rekey")
        refuse_rekey(route, facts)
        route_token = secrets.token_urlsafe(_ROUTE_TOKEN_BYTES)
        self._route.record_route_token(route.chunk_id, token_hash=TokenHash(route_token).hex, at=self._clock.now())
        return route_token
