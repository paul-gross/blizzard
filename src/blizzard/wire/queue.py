"""The ready-queue peek — the read a runner's FILL step does before a claim.

``GET /queue/peek`` returns the hub-ordered ready queue (chunks with no live route),
read-only. FILL peeks it, acquires environments for a candidate, then claims via
``POST /routes``. The ordering mechanism is the queue-ordering open
question; order derives from appended facts.
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


class QueueReorderRequest(BaseModel):
    """Move a ready chunk to a queue position — the board's Prioritize control.

    ``position`` is the target index in the ready queue, ``0`` being the top; it is
    clamped into range, so ``0`` always means "to the front". Ordering is a hub-side
    property: the move appends one position fact and the order re-derives.
    """

    chunk_id: str
    position: int = 0


class QueueReorderResponse(BaseModel):
    """The ready queue after a reorder, in its new order — the board re-renders from it."""

    entries: list[QueuePeekEntry] = []


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
