"""The single seam every mutating chunk route publishes a ``chunk-changed`` frame through (issue #212),
so every emit site enriches the frame the same way."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.hub.composition import HubServices
from blizzard.hub.domain.work import ChunkChange, ChunkFacts
from blizzard.hub.events.broker import ChunkChangeCause


@dataclass(frozen=True)
class ChunkChanged:
    """One mutating route's ``chunk-changed`` frame, held across the write it describes.

    Built before the write, so :attr:`prev_status` is a derivation over the facts as they
    stood then (``bzh:facts-not-status``), and published after."""

    services: HubServices
    chunk_id: str
    prev_status: str | None

    @classmethod
    def before(cls, services: HubServices, chunk_id: str) -> ChunkChanged:
        """The chunk's status right now; ``None`` when the chunk does not yet exist."""
        facts = services.chunks.load_facts(chunk_id)
        return cls(services, chunk_id, None if facts is None else facts.status().value)

    @classmethod
    def of(cls, services: HubServices, chunk_id: str, *, prev_status: str | None) -> ChunkChanged:
        """A frame whose "before" the caller already holds — a mint, or facts already loaded."""
        return cls(services, chunk_id, prev_status)

    def publish(
        self,
        *,
        cause: ChunkChangeCause | None,
        status: str | None = None,
        by: str | None = None,
        key: str | None = None,
    ) -> None:
        """Publish the fully enriched frame, loading the post-write facts, chunk, and pinned graph
        itself; status derives from those unless ``status`` overrides it. ``key`` (issue #213) names
        the durable fact just written, in :class:`~blizzard.hub.domain.work.ActivityRow`'s key format,
        or ``None``. ``by`` (issue #364) is the deleting operator, only ever supplied by the delete
        route. Degrades to a bare ``{chunk_id, status}`` frame, still carrying ``cause``/``prev_status``/
        ``by``, rather than raising — a delete's read-back is always this branch, since the chunk is
        gone (``bzh:facts-not-status`` has nothing left to derive from)."""
        facts = self.services.chunks.load_facts(self.chunk_id) or ChunkFacts(minted=True)
        resolved_status = status if status is not None else facts.status().value
        chunk = self.services.chunks.get(self.chunk_id)
        graph = self.services.graphs.get(chunk.graph_id) if chunk is not None else None
        if chunk is None or graph is None:
            self.services.events.publish_chunk_changed(
                self.chunk_id, resolved_status, prev_status=self.prev_status, cause=cause, by=by, key=key
            )
            return

        from_graph = None
        transition = facts.newest_transition()
        if transition is not None and transition.graph_id is not None and transition.graph_id != graph.graph_id:
            from_graph = self.services.graphs.get(transition.graph_id)

        route = self.services.chunks.route_of(self.chunk_id)
        runner_id = route.runner_id if route is not None else None

        change = ChunkChange.of(
            chunk,
            graph,
            facts,
            prev_status=self.prev_status,
            runner_id=runner_id,
            cause=cause,
            from_graph=from_graph,
        )
        self.services.events.publish_chunk_changed(
            self.chunk_id,
            resolved_status,
            prev_status=change.prev_status,
            prev_node=change.prev_node,
            node=change.node,
            runner_id=change.runner_id,
            cause=cause,  # change.cause is a widened `str | None` (`bzh:domain-core` — the
            # domain stays events-layer-free); this module already holds the typed value.
            graph_id=change.graph_id,
            by=by,
            key=key,
        )
