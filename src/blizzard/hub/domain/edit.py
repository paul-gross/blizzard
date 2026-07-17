"""Chunk build-property edits — graph and model, while the chunk is not-ready (issue #27).

Ingest pins a chunk's workflow graph and model at mint (``ingest.py``); the not-ready
resting state issue #26 opens is the one window to change either before an agent picks
the chunk up. Refused once the chunk has left ``not_ready`` — promoted, claimed, running,
or any later state: structurally mirrors :mod:`blizzard.hub.domain.pause`'s
load-facts/derive-status/compare/raise shape, just with the inverse admit set (only
``not_ready`` is editable, versus pause's exclude-list).

Both edits are plain column overwrites, not append-only facts — ``bzh:facts-not-status``
governs *status derivation*, not every mutable field, and ``graph_id`` was already a
mint-time column with no fact log behind it; ``model`` follows the same shape.

Holds the *write* chunk repository (``bzh:controller-read-only``); the route resolves the
chunk (and, for a graph edit, the target :class:`~blizzard.hub.domain.graph.Graph` —
``bzh:domain-takes-objects``) and delegates here.
"""

from __future__ import annotations

from blizzard.hub.domain.graph import Graph
from blizzard.hub.domain.work import Chunk, ChunkFacts, ChunkStatus, IWriteChunkRepository, derive_chunk_status


class ChunkNotEditable(Exception):
    """An edit targeted a chunk that has left ``not_ready`` — the only editable status."""

    def __init__(self, chunk_id: str, status: ChunkStatus) -> None:
        super().__init__(f"chunk {chunk_id} is {status.value}, not editable (only a not_ready chunk can be edited)")
        self.chunk_id = chunk_id
        self.status = status


class EditService:
    """Edit a not-ready chunk's graph or model selection (issue #27)."""

    def __init__(self, *, chunks: IWriteChunkRepository) -> None:
        self._chunks = chunks

    def set_graph(self, chunk: Chunk, *, graph: Graph) -> None:
        """Repin the chunk to ``graph``; raises :class:`ChunkNotEditable` once it has left not_ready."""
        self._require_not_ready(chunk.chunk_id)
        self._chunks.set_graph(chunk.chunk_id, graph_id=graph.graph_id)

    def set_model(self, chunk: Chunk, *, model: str) -> None:
        """Repin the chunk's model; raises :class:`ChunkNotEditable` once it has left not_ready."""
        self._require_not_ready(chunk.chunk_id)
        self._chunks.set_model(chunk.chunk_id, model=model)

    def _require_not_ready(self, chunk_id: str) -> None:
        facts = self._chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
        status = derive_chunk_status(facts)
        if status is not ChunkStatus.NOT_READY:
            raise ChunkNotEditable(chunk_id, status)
