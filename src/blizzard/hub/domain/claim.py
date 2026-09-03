"""Route claim — how a runner takes work.

The ``POST /routes`` domain rule: the hub accepts **exactly one** claim per chunk, and
the winning claim's result carries the chunk's first node envelope. A runner marked
``hub_paused`` is refused before the race is run, and only for new claims (issue #44).
The load-facts → check-live-route → record-route sequence is an atomic CAS (issue #120)."""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass

from blizzard.foundation.chunk_status import TERMINAL_STATUSES, ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.foundation.tokens import TokenHash
from blizzard.hub.domain.chunks.artifacts import IReadChunkArtifactsRepository
from blizzard.hub.domain.chunks.dependencies import IReadChunkDependenciesRepository
from blizzard.hub.domain.chunks.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunks.record import IReadChunkRecordRepository
from blizzard.hub.domain.chunks.route import IWriteChunkRouteRepository
from blizzard.hub.domain.envelope import Envelope
from blizzard.hub.domain.fleet import Route
from blizzard.hub.domain.graph import Graph, IReadGraphRepository
from blizzard.hub.domain.registry import IReadRunnerRegistry
from blizzard.hub.domain.work import Chunk
from blizzard.wire.envelope import NodeEnvelope

#: `secrets.token_urlsafe` byte count for the route capability token (43 URL-safe chars).
_ROUTE_TOKEN_BYTES = 32

# Crash point (``bzh:crash-point-registry``, issue #84b): the route and its capability-token
# fact are durable, but the plaintext has not reached the runner; recovered by claim adoption.
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
    """The claiming runner is paused at the hub registry — refused before any race (issue #44).

    Distinct from :class:`ClaimConflict`: this runner did not lose to another claimant,
    it was never eligible to claim in the first place."""

    def __init__(self, *, runner_id: str) -> None:
        super().__init__(f"runner {runner_id} is paused at the hub")
        self.runner_id = runner_id


class ClaimDeniedTerminal(Exception):
    """The chunk is already terminal ({done, stopped}) — refused before the race,
    mirroring :class:`ClaimDeniedPaused`'s shape: this is not a race loss, the chunk
    can never be claimed again. Closes the peek-then-claim window (issue #118) by
    re-deriving status fresh, under the claim lock, rather than trusting the peek."""

    def __init__(self, *, chunk_id: str, status: ChunkStatus) -> None:
        super().__init__(f"chunk {chunk_id} is {status.value}, not claimable")
        self.chunk_id = chunk_id
        self.status = status


class ClaimDeniedDependency(Exception):
    """The chunk stands on a prerequisite that has not reached ``done`` — refused before
    the race, mirroring :class:`ClaimDeniedTerminal`'s shape. Re-derived fresh under the
    claim lock (blizzard#458) so a race between a peek and a claim can never slip a
    blocked chunk through, whether the edge landed or the prerequisite finished after the
    peek. Names the one standing edge found unmet, earliest-declared first — not every
    unmet edge the chunk may carry."""

    def __init__(self, *, chunk_id: str, prerequisite_chunk_id: str) -> None:
        super().__init__(f"chunk {chunk_id} depends on unmet prerequisite {prerequisite_chunk_id}")
        self.chunk_id = chunk_id
        self.prerequisite_chunk_id = prerequisite_chunk_id


@dataclass(frozen=True)
class ClaimResult:
    """A won claim — the route fact, its first node envelope, and the route's plaintext
    capability token (issue #84a). ``route_token`` is returned exactly once, here; only
    its sha256 hash is persisted. ``route_id`` (issue #213) is the freshly-minted route
    id, which :class:`~blizzard.hub.domain.fleet.Route` itself does not carry."""

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
        record: IReadChunkRecordRepository,
        facts: IReadChunkFactsRepository,
        artifacts: IReadChunkArtifactsRepository,
        dependencies: IReadChunkDependenciesRepository,
        graphs: IReadGraphRepository,
        registry: IReadRunnerRegistry,
        clock: IClock,
        claim_lock: threading.Lock,
    ) -> None:
        self._route = route
        self._record = record
        self._facts = facts
        self._artifacts = artifacts
        self._dependencies = dependencies
        # Re-resolves the chunk's graph fresh under the lock — see `_claim_locked`.
        self._graphs = graphs
        self._registry = registry
        self._clock = clock
        # Serializes the check-live-route → record-route CAS; shared with the edit path
        # so a concurrent edit and claim resolve to exactly one winner (issue #120).
        self._claim_lock = claim_lock

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
        if registration is not None and registration.hub_paused:
            raise ClaimDeniedPaused(runner_id=runner_id)
        with self._claim_lock:
            return self._claim_locked(
                chunk, graph, runner_id=runner_id, workspace_id=workspace_id, environment_ids=environment_ids
            )

    def _claim_locked(
        self,
        chunk: Chunk,
        graph: Graph,
        *,
        runner_id: str,
        workspace_id: str,
        environment_ids: list[str],
    ) -> ClaimResult:
        existing = self._route.route_of(chunk.chunk_id)
        if existing is not None:
            raise ClaimConflict(held_by_runner_id=existing.runner_id)

        # Re-read the chunk under the lock: an edit that landed first (issue #120) may have
        # moved `graph_id`/`model` since the edge resolved the handed-in objects.
        current = self._record.get(chunk.chunk_id)
        if current is None:  # pragma: no cover - the chunk cannot vanish mid-claim
            raise ClaimConflict(held_by_runner_id=runner_id)
        if current.graph_id != chunk.graph_id:
            fresh_graph = self._graphs.get(current.graph_id)
            if fresh_graph is None:  # pragma: no cover - a pinned graph always resolves
                raise ClaimConflict(held_by_runner_id=runner_id)
            graph = fresh_graph
        chunk = current

        facts = self._facts.load_facts(chunk.chunk_id)
        # Re-derive status fresh under the claim lock: a stop landing between this
        # runner's peek and its claim POST is invisible to the peek (issue #118).
        status = facts.status() if facts is not None else ChunkStatus.NOT_READY
        if status in TERMINAL_STATUSES:
            raise ClaimDeniedTerminal(chunk_id=chunk.chunk_id, status=status)

        # Re-derived fresh under the same lock (blizzard#458): a declared edge or a
        # prerequisite's completion landing after this runner's peek is invisible to the
        # peek, exactly as a terminal transition is (issue #118).
        unmet = self._unmet_prerequisite(chunk.chunk_id)
        if unmet is not None:
            raise ClaimDeniedDependency(chunk_id=chunk.chunk_id, prerequisite_chunk_id=unmet)

        # The claim carries the current epoch (0 before the first lease report) and mints
        # no lease of its own; the fence consumes the runner's reported epoch, not this.
        epoch = facts.latest_epoch() or 0 if facts is not None else 0
        now = self._clock.now()

        route = Route(
            chunk_id=chunk.chunk_id,
            runner_id=runner_id,
            workspace_id=workspace_id,
            environment_ids=list(environment_ids),
            created_at=now,
        )
        # Minted fresh per acquisition (issue #84a): the plaintext is returned once and
        # never stored — only its sha256 hash lands, in the same write as record_route.
        route_token = secrets.token_urlsafe(_ROUTE_TOKEN_BYTES)
        route_id = self._route.record_route(route, token_hash=TokenHash(route_token).hex, at=now)
        _CP_CLAIM_AFTER_PERSIST_BEFORE_RESPONSE.reached()

        node_id = (facts.current_node_id() if facts is not None else None) or graph.entry_node_id
        node = graph.node_by_id(node_id)
        if node is None:  # pragma: no cover - a pinned graph always resolves its own node
            raise ClaimConflict(held_by_runner_id=runner_id)
        envelope = Envelope(
            chunk=chunk,
            graph=graph,
            node=node,
            artifacts=self._artifacts.load_artifacts(chunk.chunk_id),
            epoch=epoch,
            entered_by_restart=facts is not None and facts.entered_by_restart(),
        ).wire
        return ClaimResult(route=route, envelope=envelope, route_token=route_token, route_id=route_id)

    def _unmet_prerequisite(self, chunk_id: str) -> str | None:
        """The earliest-declared standing edge naming ``chunk_id`` as dependent whose
        prerequisite has not reached ``done`` — ``None`` when every standing edge is met
        or the chunk carries none. Filters the full standing set rather than a targeted
        read, mirroring ``DependencyService``'s own cycle check: the read seam answers
        two questions and no more (``blizzard.hub.domain.chunks.dependencies``)."""
        edges = [e for e in self._dependencies.list_standing_edges() if e.dependent_chunk_id == chunk_id]
        for edge in edges:
            prerequisite_facts = self._facts.load_facts(edge.prerequisite_chunk_id)
            status = prerequisite_facts.status() if prerequisite_facts is not None else ChunkStatus.NOT_READY
            if status != ChunkStatus.DONE:
                return edge.prerequisite_chunk_id
        return None

    def rekey(self, route: Route) -> str:
        """Rotate a live route's capability token (issue #84b) — the lost-plaintext
        recovery: a claim whose route-token response was never read back has no other
        way to learn it. Appends a new ``route_token_minted`` fact rather than mutating
        the prior one (``bzh:facts-not-status``); newest-fact-wins supersedes the old
        token, re-run idempotent. Takes an already-resolved route (``bzh:domain-takes-objects``)."""
        route_token = secrets.token_urlsafe(_ROUTE_TOKEN_BYTES)
        self._route.record_route_token(route.chunk_id, token_hash=TokenHash(route_token).hex, at=self._clock.now())
        return route_token
