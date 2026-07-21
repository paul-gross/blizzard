"""The ready-queue peek — the read a runner's FILL step does before a claim.

``GET /api/queue`` (and the runner's fleet-side ``GET /api/fleet/queue/peek``) returns
the hub-ordered ready queue (chunks with no live route), read-only. FILL peeks it,
acquires environments for a candidate, then claims via ``POST /routes``. The ordering
mechanism is the queue-ordering open question; order derives from appended facts.
"""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.wire.chunk import PmPointerModel


class QueuePeekEntry(BaseModel):
    """One ready chunk, in queue order."""

    chunk_id: str
    graph_id: str
    position: int
    pm_pointers: list[PmPointerModel] = []


class QueuePeekResponse(BaseModel):
    """The ready queue as peeked by FILL, in the hub's explicit order."""

    entries: list[QueuePeekEntry] = []


class QueueReplaceRequest(BaseModel):
    """Idempotent whole-order replacement of the ready queue — ``PUT /api/queue``.

    ``chunk_ids`` is the desired order, front to back; every id must name a
    currently-ready chunk (``409`` otherwise) and must not repeat (``422``
    otherwise). A ready chunk not named here keeps its current relative order,
    appended after the named ones.
    """

    chunk_ids: list[str]


class ChunkGroupRequest(BaseModel):
    """Merge unacquired chunks into one — the board's Group control.

    ``merge_chunk_ids`` are the ready chunks folded into the path's survivor chunk; the
    survivor absorbs the union of their PM pointers and the merged chunks are discarded as
    ephemeral. Self-references and duplicates are ignored; a non-ready member is
    rejected ``409``.
    """

    merge_chunk_ids: list[str]


class ChunkGroupResponse(BaseModel):
    """The survivor chunk after a group — its id and the union of PM pointers it carries."""

    chunk_id: str
    pm_pointers: list[PmPointerModel] = []
    merged_chunk_ids: list[str] = []
