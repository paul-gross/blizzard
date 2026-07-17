"""Route claim — how a runner takes work.

The ``POST /routes`` domain rule: acquisition is the birth of a **complete** route
fact. The hub accepts **exactly one** claim per chunk — a second claim on a chunk
that already has a live route loses with a :class:`ClaimConflict` (surfaced 409),
and the winning claim's result carries the chunk's first node envelope so the runner
starts working without a second round-trip.

The single-claim guarantee is the hub's single-writer property: the daemon
is the fleet's one arbiter, so the load-facts → check-live-route →
record-route sequence must run as an atomic compare-and-set. FastAPI serves sync
routes from a threadpool, so two runners' claims can arrive concurrently; a
per-service lock serializes the CAS (the hub is one process — an in-process lock is
the whole arbitration surface, cross-machine or not). The claim does **not** mint the
executing lease
: the runner mints it and reports ``lease.minted`` up through its outbound
buffer to ``POST /events``, and the completion fence checks against that. The
claim envelope carries the chunk's current epoch (``latest`` reported so far, or 0
before the runner's first lease report) so the worker starts without a round-trip;
the runner's own lease epoch — not this value — is what the fence consumes.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.envelope import build_node_envelope
from blizzard.hub.domain.fleet import Route
from blizzard.hub.domain.graph import Graph
from blizzard.hub.domain.work import Chunk, IWriteChunkRepository, current_node_id, latest_epoch
from blizzard.wire.envelope import NodeEnvelope


class ClaimConflict(Exception):
    """The chunk already has a live route — this claim lost the race."""

    def __init__(self, *, held_by_runner_id: str) -> None:
        super().__init__(f"chunk already claimed by runner {held_by_runner_id}")
        self.held_by_runner_id = held_by_runner_id


@dataclass(frozen=True)
class ClaimResult:
    """A won claim — the route fact plus the chunk's first node envelope."""

    route: Route
    envelope: NodeEnvelope


class ClaimService:
    """Claim a chunk for a runner, exactly-one-wins."""

    def __init__(self, *, chunks: IWriteChunkRepository, clock: IClock) -> None:
        self._chunks = chunks
        self._clock = clock
        # Serializes the check-live-route → record-route CAS across concurrent claims
        # on one hub daemon. One ClaimService per hub, so one lock guards every
        # chunk's claim; contention is a claim-rate concern, not a correctness one.
        self._claim_lock = threading.Lock()

    def claim(
        self,
        chunk: Chunk,
        graph: Graph,
        *,
        runner_id: str,
        workspace_id: str,
        environment_ids: list[str],
    ) -> ClaimResult:
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
        existing = self._chunks.route_of(chunk.chunk_id)
        if existing is not None:
            raise ClaimConflict(held_by_runner_id=existing.runner_id)

        facts = self._chunks.load_facts(chunk.chunk_id)
        # The runner mints the lease and reports its epoch via POST /events;
        # the claim only carries the current epoch (0 before the first report) into
        # the envelope, and does not itself write a lease fact.
        epoch = latest_epoch(facts) or 0 if facts is not None else 0
        now = self._clock.now()

        route = Route(
            chunk_id=chunk.chunk_id,
            runner_id=runner_id,
            workspace_id=workspace_id,
            environment_ids=list(environment_ids),
            created_at=now,
        )
        self._chunks.record_route(route, at=now)

        node_id = (current_node_id(facts) if facts is not None else None) or graph.entry_node_id
        node = graph.node_by_id(node_id)
        if node is None:  # pragma: no cover - a pinned graph always resolves its own node
            raise ClaimConflict(held_by_runner_id=runner_id)
        envelope = build_node_envelope(
            chunk=chunk,
            node=node,
            artifacts=self._chunks.load_artifacts(chunk.chunk_id),
            epoch=epoch,
        )
        return ClaimResult(route=route, envelope=envelope)
