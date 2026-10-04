"""Route claim — how a runner takes work.

The ``POST /routes`` domain rule: the hub accepts **exactly one** claim per chunk, and
the winning claim's result carries the chunk's first node envelope. A runner marked
``hub_paused`` is refused before the race is run, and only for new claims.
The load-facts → check-live-route → record-route sequence is an atomic CAS."""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.foundation.roles import dto
from blizzard.foundation.tokens import TokenHash
from blizzard.hub.domain.chunk.model import Chunk, WorkRefLabel, holds_claim
from blizzard.hub.domain.chunk.ports.artifacts import IReadChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.exclusive import IChunkExclusiveWrites, ILockedChunkRead
from blizzard.hub.domain.chunk.ports.route import IWriteChunkRouteRepository
from blizzard.hub.domain.execution.eligibility import EligibilityCheck
from blizzard.hub.domain.execution.envelope import Arrival, Envelope
from blizzard.hub.domain.graph.model import Graph, IReadGraphRepository
from blizzard.hub.domain.runners.registration import IReadRunnerRegistry, RetiredRunnerGuard
from blizzard.hub.domain.runners.route import Route
from blizzard.wire.envelope import NodeEnvelope

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
    reporting no capabilities never reaches this check (see ``_claim_locked``)."""

    def __init__(self, *, chunk_id: str, runner_id: str) -> None:
        super().__init__(f"runner {runner_id}'s capabilities no longer satisfy chunk {chunk_id}")
        self.chunk_id = chunk_id
        self.runner_id = runner_id


@dto
@dataclass(frozen=True)
class ClaimResult:
    """A won claim — the route fact, its first node envelope, and the route's plaintext
    capability token. ``route_token`` is returned exactly once, here; only
    its sha256 hash is persisted. ``route_id`` is the freshly-minted route
    id, which :class:`~blizzard.hub.domain.runners.route.Route` itself does not carry."""

    route: Route
    envelope: NodeEnvelope
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
        registration = self._registry.get_runner(runner_id)
        if registration is not None:
            registration.refuse_if_retired(action="claim")
        if registration is not None and registration.hub_paused:
            raise ClaimDeniedPaused(runner_id=runner_id)
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
        # Re-derived under the lock (a stop can land after the peek), and before the
        # route: a route left on a terminal chunk confers no tenure.
        status = facts.status() if facts is not None else ChunkStatus.NOT_READY
        if not holds_claim(status):
            raise ClaimDeniedTerminal(chunk_id=chunk.chunk_id, status=status)

        existing = handle.route_of(chunk.chunk_id)
        if existing is not None:
            raise ClaimConflict(held_by_runner_id=existing.runner_id)

        # Re-derived under the same lock: an edge or completion can land after the peek.
        unmet = self._unmet_prerequisite(handle, chunk.chunk_id)
        if unmet is not None:
            raise ClaimDeniedDependency(chunk_id=chunk.chunk_id, prerequisite_chunk_id=unmet)

        # Hoisted ahead of the mint (below) so the same resolved node serves both the
        # incompatibility check and the envelope, rather than resolving it twice.
        node_id = (facts.current_node_id() if facts is not None else None) or graph.entry_node_id
        node = graph.node_by_id(node_id)
        if node is None:  # pragma: no cover - a pinned graph always resolves its own node
            raise ClaimConflict(held_by_runner_id=runner_id)

        # Re-fetched fresh under the lock, never the pre-lock read the
        # paused guard used: a capability change landing after this runner's peek must not race the claim.
        registration = handle.runner_registration(runner_id)
        if registration is not None:
            registration.refuse_if_retired(action="claim")
        if registration is not None and registration.capabilities:
            eligible = EligibilityCheck(chunk, graph, node, registration.capabilities).eligible
            if not eligible:
                raise ClaimDeniedIncompatible(chunk_id=chunk.chunk_id, runner_id=runner_id)

        # The claim carries the current epoch (0 before the first lease report) and mints
        # no lease of its own.
        epoch = facts.latest_epoch() or 0 if facts is not None else 0
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
        envelope = Envelope(
            chunk=chunk,
            graph=graph,
            node=node,
            artifacts=self._artifacts.load_artifacts(chunk.chunk_id),
            epoch=epoch,
            arrival_addendum=Arrival.of_facts(graph, facts).addendum,
            entered_by_restart=facts is not None and facts.entered_by_restart(),
            label=self._label,
        ).wire
        return ClaimResult(route=route, envelope=envelope, route_token=route_token, route_id=route_id)

    def _unmet_prerequisite(self, handle: ILockedChunkRead, chunk_id: str) -> str | None:
        """The earliest-declared standing edge naming ``chunk_id`` as dependent whose
        prerequisite has not reached ``done`` — ``None`` when every standing edge is met
        or the chunk carries none. Filters the full standing set rather than a targeted
        read, mirroring ``DependencyService``'s own cycle check, resolving every
        prerequisite's facts with one bulk ``facts_for`` call rather than one per edge."""
        edges = [e for e in handle.standing_edges() if e.dependent_chunk_id == chunk_id]
        facts_by_id = handle.facts_for([edge.prerequisite_chunk_id for edge in edges])
        for edge in edges:
            prerequisite_facts = facts_by_id.get(edge.prerequisite_chunk_id)
            status = prerequisite_facts.status() if prerequisite_facts is not None else ChunkStatus.NOT_READY
            if status != ChunkStatus.DONE:
                return edge.prerequisite_chunk_id
        return None

    def rekey(self, route: Route) -> str:
        """Rotate a live route's capability token — the lost-plaintext
        recovery: a claim whose route-token response was never read back has no other
        way to learn it. Appends a new ``route_token_minted`` fact rather than mutating
        the prior one (``bzh:facts-not-status``); newest-fact-wins supersedes the old
        token, re-run idempotent. Takes an already-resolved route (``bzh:domain-takes-objects``)."""
        self._retired.refuse_if_retired(route.runner_id, action="route-token rekey")
        route_token = secrets.token_urlsafe(_ROUTE_TOKEN_BYTES)
        self._route.record_route_token(route.chunk_id, token_hash=TokenHash(route_token).hex, at=self._clock.now())
        return route_token
