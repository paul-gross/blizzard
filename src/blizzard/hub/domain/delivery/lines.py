"""Building a flow line — the one way any reader judges submissions against where their chunks stand, so a place a
surface shows is judged exactly as the driver judges a turn."""

from __future__ import annotations

from collections.abc import Sequence

from blizzard.hub.domain.chunk.model import ChunkFacts
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.delivery.flows import FlowLine, PollPolicy, Submission
from blizzard.hub.domain.graph.model import IReadManyGraphs, Node


class FlowLineFactory:
    """Reads what a :class:`FlowLine` judges by — each submission's chunk's facts and its node's poll cadence — in a
    fixed number of batched reads, however many submissions it is handed."""

    def __init__(
        self, *, facts: IReadChunkFactsRepository, record: IReadChunkRecordRepository, graphs: IReadManyGraphs
    ) -> None:
        self._facts = facts
        self._record = record
        self._graphs = graphs

    def line(self, submissions: Sequence[Submission], *, turning: frozenset[str]) -> FlowLine:
        """The line of ``submissions``, ``turning`` naming those with an open turn. A submission whose node its
        chunk's graph no longer holds polls at the default cadence."""
        chunk_ids = sorted({sub.chunk_id for sub in submissions})
        loaded = self._facts.load_facts_for(chunk_ids) if chunk_ids else {}
        nodes = self.nodes(chunk_ids)
        return FlowLine(
            submissions=tuple(submissions),
            facts={chunk_id: ChunkFacts.or_default(loaded.get(chunk_id)) for chunk_id in chunk_ids},
            policies={
                sub.submission_id: PollPolicy.of(node)
                if (node := nodes.get((sub.chunk_id, sub.node_id)))
                else PollPolicy()
                for sub in submissions
            },
            turning=turning,
        )

    def nodes(self, chunk_ids: Sequence[str]) -> dict[tuple[str, str], Node]:
        """Each named chunk's graph's nodes, by ``(chunk_id, node_id)``."""
        if not chunk_ids:
            return {}
        chunks = self._record.get_many(list(chunk_ids))
        graphs = self._graphs.get_many(sorted({chunk.graph_id for chunk in chunks.values()}))
        return {
            (chunk.chunk_id, node.node_id): node
            for chunk in chunks.values()
            if (graph := graphs.get(chunk.graph_id)) is not None
            for node in graph.nodes
        }
