"""Route claim — how a runner takes work (D-021/D-080).

The ``POST /routes`` domain rule: acquisition is the birth of a **complete** route
fact. The hub accepts **exactly one** claim per chunk — a second claim on a chunk
that already has a live route loses with a :class:`ClaimConflict` (surfaced 409),
and the winning claim's result carries the chunk's first node envelope so the runner
starts working without a second round-trip.

The single-claim guarantee is the hub's single-writer property (D-023): the daemon
serializes writes, so the load-facts → check-live-route → record-route sequence here
is effectively a compare-and-set. In the walking skeleton the claim also **mints the
executing lease** (epoch = latest + 1, so a first claim is epoch 1) as a stand-in for
the runner-minted lease reported via ``POST /events`` (P7) — the envelope carries that
epoch, and the completion fence checks against it.
"""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.envelope import build_node_envelope
from blizzard.hub.domain.fleet import Route
from blizzard.hub.domain.graph import Graph
from blizzard.hub.domain.work import Chunk, IWriteChunkRepository, current_node_id, latest_epoch
from blizzard.wire.envelope import NodeEnvelope


class ClaimConflict(Exception):
    """The chunk already has a live route — this claim lost the race (D-080)."""

    def __init__(self, *, held_by_runner_id: str) -> None:
        super().__init__(f"chunk already claimed by runner {held_by_runner_id}")
        self.held_by_runner_id = held_by_runner_id


@dataclass(frozen=True)
class ClaimResult:
    """A won claim — the route fact plus the chunk's first node envelope."""

    route: Route
    envelope: NodeEnvelope


class ClaimService:
    """Claim a chunk for a runner, exactly-one-wins (D-080)."""

    def __init__(self, *, chunks: IWriteChunkRepository, clock: IClock) -> None:
        self._chunks = chunks
        self._clock = clock

    def claim(
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
        epoch = (latest_epoch(facts) or 0) + 1 if facts is not None else 1
        now = self._clock.now()
        self._chunks.record_lease(chunk.chunk_id, epoch=epoch, runner_id=runner_id, at=now)

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
