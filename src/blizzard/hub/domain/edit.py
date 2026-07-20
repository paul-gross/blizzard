"""Chunk build-property edits — graph and model, while the chunk sits unclaimed
(issue #27, widened by issue #120).

Ingest pins a chunk's workflow graph and model at mint (``ingest.py``); the not-ready
resting state issue #26 opens was the first window to change either before an agent
picks the chunk up. Issue #120 widens that window through promote: a chunk that has
left ``not_ready`` but sits ``ready`` with no live route is still editable — the wrong
graph is often noticed only after promote, with no runner anywhere near the chunk yet.
Refused once the chunk is actually claimed — ``running``, ``delivering``,
``waiting_on_human``, ``needs_human``, ``paused`` (post-claim), ``done``, or
``stopped``: structurally mirrors :mod:`blizzard.hub.domain.pause`'s
load-facts/derive-status/compare/raise shape, just with the admit set naming
``{not_ready, ready}`` rather than an exclude-list.

Both edits are plain column overwrites, not append-only facts — ``bzh:facts-not-status``
governs *status derivation*, not every mutable field, and ``graph_id`` was already a
mint-time column with no fact log behind it; ``model`` follows the same shape.

Widening the admit set to ``ready`` opens the edit window onto the same chunk a
runner's claim (:class:`~blizzard.hub.domain.claim.ClaimService`) can land against
concurrently — both are now check-then-act sequences over "does this chunk have a
live route", and an unguarded pair is a torn read: the edit's status check could pass
just before a claim lands, then write against a chunk that is now leased. Rather than
inventing a second synchronization mechanism, this service is handed the **same**
in-process lock :class:`~blizzard.hub.domain.claim.ClaimService` serializes its own
check-live-route/record-route CAS with (one lock per hub, injected at the composition
root — ``bzh:dependency-injection``): a claim and an edit racing the same chunk now
resolve to exactly one of "the edit sees the live route and 409s" or "the edit's write
already landed before the claim acquired the lock, and the claim proceeds after it" —
never a mix of both.

Holds the *write* chunk repository (``bzh:controller-read-only``); the route resolves the
chunk (and, for a graph edit, the target :class:`~blizzard.hub.domain.graph.Graph` —
``bzh:domain-takes-objects``) and delegates here.
"""

from __future__ import annotations

import threading

from blizzard.hub.domain.graph import Graph, IReadGraphRepository
from blizzard.hub.domain.work import Chunk, ChunkFacts, ChunkStatus, IWriteChunkRepository, derive_chunk_status

#: The admit set widened by issue #120 — a chunk is editable while resting
#: ``not_ready`` (issue #27's original window) or ``ready`` with no live route; every
#: other status means a runner has (or had) the chunk and the pin is sealed.
_EDITABLE = frozenset({ChunkStatus.NOT_READY, ChunkStatus.READY})


class ChunkNotEditable(Exception):
    """An edit targeted a chunk outside the editable window (``not_ready``/``ready``-unclaimed)."""

    def __init__(self, chunk_id: str, status: ChunkStatus) -> None:
        super().__init__(
            f"chunk {chunk_id} is {status.value}, not editable "
            "(only a not_ready or a ready-and-unclaimed chunk can be edited)"
        )
        self.chunk_id = chunk_id
        self.status = status


class TargetGraphRetired(Exception):
    """A graph edit named a graph that has since been retired (issue #101)."""

    def __init__(self, graph_id: str) -> None:
        super().__init__(f"graph {graph_id} is retired and cannot receive new work")
        self.graph_id = graph_id


class EditService:
    """Edit a not-ready or ready-unclaimed chunk's graph or model selection (issue #27, #120)."""

    def __init__(
        self, *, chunks: IWriteChunkRepository, graphs: IReadGraphRepository, claim_lock: threading.Lock
    ) -> None:
        self._chunks = chunks
        self._graphs = graphs
        # The same lock ClaimService serializes its claim CAS with — see the module
        # docstring's race-atomicity note (issue #120).
        self._claim_lock = claim_lock

    def set_graph(self, chunk: Chunk, *, graph: Graph) -> None:
        """Repin the chunk to ``graph``.

        Raises :class:`ChunkNotEditable` once claimed or later, checked first — the
        chunk's own editability is the more fundamental refusal, so it wins even when
        ``graph`` also happens to be retired. Only once the chunk is confirmed
        editable is ``graph`` checked for retirement, raising :class:`TargetGraphRetired`
        (issue #101) — both checks run under the same lock as the write so neither can
        be answered by a state that has since moved on.
        """
        with self._claim_lock:
            self._require_editable(chunk.chunk_id)
            if self._graphs.is_retired(graph.graph_id):
                raise TargetGraphRetired(graph.graph_id)
            self._chunks.set_graph(chunk.chunk_id, graph_id=graph.graph_id)

    def set_model(self, chunk: Chunk, *, model: str) -> None:
        """Repin the chunk's model; raises :class:`ChunkNotEditable` once claimed or later."""
        with self._claim_lock:
            self._require_editable(chunk.chunk_id)
            self._chunks.set_model(chunk.chunk_id, model=model)

    def _require_editable(self, chunk_id: str) -> None:
        facts = self._chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
        status = derive_chunk_status(facts)
        if status not in _EDITABLE:
            raise ChunkNotEditable(chunk_id, status)
