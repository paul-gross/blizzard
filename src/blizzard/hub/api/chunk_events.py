"""The single seam every mutating chunk route publishes a ``chunk-changed`` frame through,
so every emit site enriches the frame the same way."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from blizzard.foundation.roles import dto
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.chunk.model import Chunk, ChunkChange, ChunkFacts
from blizzard.hub.domain.graph.model import Graph
from blizzard.hub.domain.runners.route import Route
from blizzard.hub.events.broker import ChunkChangeCause


@dto
@dataclass(frozen=True)
class ChunkFrameState:
    """One chunk's fully-loaded post-write state — everything a ``chunk-changed`` frame's
    enrichment reads. A missing chunk or graph leaves ``from_graph`` as ``None``;
    ``route`` reflects the loader's route read."""

    facts: ChunkFacts
    chunk: Chunk | None
    graph: Graph | None
    from_graph: Graph | None
    route: Route | None

    @classmethod
    def load(cls, services: HubServices, chunk_id: str) -> ChunkFrameState:
        """The singular reads a single-chunk write verb's ``publish`` issues."""
        facts = ChunkFacts.or_default(services.chunks.facts.load_facts(chunk_id))
        chunk = services.chunks.record.get(chunk_id)
        graph = services.graphs.get(chunk.graph_id) if chunk is not None else None
        if chunk is None or graph is None:
            return cls(facts=facts, chunk=chunk, graph=graph, from_graph=None, route=None)

        off_pin = facts.transition_graph_off_pin(graph.graph_id)
        from_graph = services.graphs.get(off_pin) if off_pin is not None else None

        route = services.chunks.route.route_of(chunk_id)
        return cls(facts=facts, chunk=chunk, graph=graph, from_graph=from_graph, route=route)


def load_frame_states(services: HubServices, chunk_ids: Sequence[str]) -> dict[str, ChunkFrameState]:
    """`ChunkFrameState.load`'s batched sibling (`bzh:bulk-reconstitution`) — one snapshot per distinct
    requested id, through the four plurals. Reads every distinct chunk's facts and
    record once, then the union of pinned and cross-graph newest-transition graph ids
    once, then every chunk's route once — a bounded number of statements regardless of
    how many ids or facts the batch names."""
    ids = list(dict.fromkeys(chunk_ids))
    facts_by_id = services.chunks.facts.load_facts_for(ids)
    chunks_by_id = services.chunks.record.get_many(ids)

    graph_ids: set[str] = {chunk.graph_id for chunk in chunks_by_id.values()}
    for chunk_id in ids:
        facts = facts_by_id.get(chunk_id)
        transition = facts.newest_transition() if facts is not None else None
        if transition is not None and transition.graph_id is not None:
            graph_ids.add(transition.graph_id)
    graphs_by_id = services.graphs.get_many(list(graph_ids))
    routes_by_id = services.chunks.route.routes_for(ids)

    states: dict[str, ChunkFrameState] = {}
    for chunk_id in ids:
        facts = ChunkFacts.or_default(facts_by_id.get(chunk_id))
        chunk = chunks_by_id.get(chunk_id)
        graph = graphs_by_id.get(chunk.graph_id) if chunk is not None else None
        from_graph = None
        if chunk is not None and graph is not None:
            off_pin = facts.transition_graph_off_pin(graph.graph_id)
            from_graph = graphs_by_id.get(off_pin) if off_pin is not None else None
        states[chunk_id] = ChunkFrameState(
            facts=facts, chunk=chunk, graph=graph, from_graph=from_graph, route=routes_by_id.get(chunk_id)
        )
    return states


@dataclass(frozen=True)
class ChunkChanged:
    """One mutating route's ``chunk-changed`` frame, held across the write it describes.

    Built before the write, so :attr:`prev_status` is a derivation over the facts as they
    stood then (``bzh:facts-not-status``), and published after."""

    services: HubServices
    chunk_id: str
    prev_status: str | None
    #: The pre-write facts `before()` loaded, for a domain gate to reuse; unset from `of()`.
    facts: ChunkFacts | None = None

    @classmethod
    def before(cls, services: HubServices, chunk_id: str) -> ChunkChanged:
        """The chunk's status right now, and the facts it derives from — ``status`` is
        ``None`` and ``facts`` unset when the chunk does not yet exist."""
        facts = services.chunks.facts.load_facts(chunk_id)
        return cls(services, chunk_id, None if facts is None else facts.status().value, facts)

    @classmethod
    def before_many(cls, services: HubServices, chunk_ids: Sequence[str]) -> dict[str, ChunkChanged]:
        """`before`'s batched sibling (`bzh:bulk-reconstitution`) — one snapshot per
        distinct requested id, through the narrowed `status_facts_for`. A snapshot
        carries ``prev_status`` only: ``facts`` stays ``None`` so a narrowed projection is
        never reused as the wide one. An id the plural drops gets ``prev_status=None``,
        exactly as `before` does for a missing chunk."""
        ids = list(dict.fromkeys(chunk_ids))
        facts_by_id = services.chunks.facts.status_facts_for(ids)
        result: dict[str, ChunkChanged] = {}
        for chunk_id in ids:
            facts = facts_by_id.get(chunk_id)
            result[chunk_id] = cls(services, chunk_id, None if facts is None else facts.status().value)
        return result

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
    ) -> ChunkFacts:
        """Publish the fully enriched frame, loading the post-write facts, chunk, and pinned
        graph, and return those facts. ``key`` names the durable fact just written, or
        ``None``. ``by`` (delete-route-only) still degrades a gone chunk to a bare
        ``{chunk_id, status}`` frame rather than raising."""
        state = ChunkFrameState.load(self.services, self.chunk_id)
        return self.publish_from(state, cause=cause, status=status, by=by, key=key)

    def publish_from(
        self,
        state: ChunkFrameState,
        *,
        cause: ChunkChangeCause | None,
        status: str | None = None,
        by: str | None = None,
        key: str | None = None,
    ) -> ChunkFacts:
        """`publish`'s sibling for a caller already holding the post-write state — a batch
        that loaded every touched chunk's state once through `load_frame_states`."""
        facts = state.facts
        resolved_status = status if status is not None else facts.status().value
        if state.chunk is None or state.graph is None:
            self.services.events.publish_chunk_changed(
                self.chunk_id, resolved_status, prev_status=self.prev_status, cause=cause, by=by, key=key
            )
            return facts

        runner_id = state.route.runner_id if state.route is not None else None
        change = ChunkChange.of(
            state.chunk,
            state.graph,
            facts,
            prev_status=self.prev_status,
            runner_id=runner_id,
            cause=cause,
            from_graph=state.from_graph,
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
        return facts
