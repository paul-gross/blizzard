"""Rendering one chunk as the rows and aggregates the board reads."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.graph_names import GraphNames
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.work import Chunk, ChunkFacts, holds_claim
from blizzard.wire.chunk import ChunkSummary, ChunkUsageTotalView, WorkRefView


@dataclass(frozen=True)
class ChunkView:
    """One chunk read — the row, the facts every derived value comes from
    (``bzh:facts-not-status``), and the graph resolver the read shares.

    A whole-fleet read hands :meth:`of` one :class:`GraphNames` for every chunk, so the
    graphs a fleet shares are resolved once across the list rather than once per row."""

    services: HubServices
    chunk: Chunk
    facts: ChunkFacts
    names: GraphNames

    @classmethod
    def of(cls, services: HubServices, chunk: Chunk, names: GraphNames | None = None) -> ChunkView:
        return cls(
            services=services,
            chunk=chunk,
            facts=services.chunks.load_facts(chunk.chunk_id) or ChunkFacts(minted=True),
            names=names or GraphNames(services.graphs.get),
        )

    def summary(self) -> ChunkSummary:
        """The derived fleet-list row (issue #104) — rendered both by the list read and by
        every transition verb, from the same facts (``canon:one-owner``)."""
        node_id, node_name = self.current_node()
        status = self.facts.status()
        # A finished chunk holds no claim (issue #140) — the rule is `holds_claim`'s. Asked
        # before the read so a terminal chunk costs no `route_of` query at all.
        route = self.services.chunks.route_of(self.chunk.chunk_id) if holds_claim(status) else None
        completed_at = self.facts.completed_at()
        return ChunkSummary(
            chunk_id=self.chunk.chunk_id,
            graph_id=self.chunk.graph_id,
            status=status,
            current_node_id=node_id,
            current_node_name=node_name,
            work_refs=self.pointer_views(),
            default_model=list(self.chunk.default_model),
            default_effort=self.chunk.default_effort,
            runner_id=route.runner_id if route is not None else None,
            environment_count=len(route.environment_ids) if route is not None else 0,
            cost=self.usage_total(),
            completed_at=iso_utc(completed_at) if completed_at is not None else None,
        )

    def current_node(self) -> tuple[str | None, str | None]:
        """The chunk's current node as ``(id, name)`` — the newest transition's target, or the
        pinned graph's entry node before the first transition (a nicer board value than ``None``).
        The name rides along so the board is legible without reassembly."""
        graph = self.names.graph(self.chunk.graph_id)
        node_id = self.facts.current_node_id() or (graph.entry_node_id if graph is not None else None)
        return node_id, self.names.node_name(self.chunk.graph_id, node_id)

    def pointer_views(self) -> list[WorkRefView]:
        """Each pointer with its board-legible label and browser URL — both null when no
        configured source names ``pointer.source``.

        Each pointer resolves to its own binding by name, so a chunk's pointers need not
        all share one source."""
        views: list[WorkRefView] = []
        for p in self.chunk.work_refs:
            source = self.services.work_sources.get(p.source)
            views.append(
                WorkRefView(
                    source=p.source,
                    ref=p.ref,
                    label=source.label(p) if source is not None else None,
                    web_url=source.web_url(p) if source is not None else None,
                )
            )
        return views

    def usage_total(self) -> ChunkUsageTotalView:
        usage = self.facts.usage_total()
        return ChunkUsageTotalView(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_create_tokens=usage.cache_create_tokens,
            cost_usd=usage.cost_usd,
            cost_partial=usage.cost_partial,
        )
