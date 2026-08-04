"""The ready-queue peek — the read a runner's FILL step does before a claim.

``GET /api/queue`` (and the runner's fleet-side ``GET /api/fleet/queue/peek``) returns
the hub-ordered ready queue (chunks with no live route), read-only. Order derives from
appended facts.
"""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.wire.chunk import WorkRefModel


class QueuePeekEntry(BaseModel):
    """One ready chunk, in queue order."""

    chunk_id: str
    graph_id: str
    position: int
    work_refs: list[WorkRefModel] = []


class QueuePeekResponse(BaseModel):
    """The ready queue, in the hub's explicit order."""

    entries: list[QueuePeekEntry] = []


class QueueReplaceRequest(BaseModel):
    """Idempotent whole-order replacement of the ready queue — ``PUT /api/queue``.

    ``chunk_ids`` is the desired order, front to back; every id must name a
    currently-ready chunk (``409`` otherwise) and must not repeat (``422``
    otherwise). A ready chunk not named here keeps its current relative order,
    appended after the named ones.
    """

    chunk_ids: list[str]


class QueuePositionRequest(BaseModel):
    """Single-chunk fractional reposition — ``POST /api/queue/position`` (issue #137).

    ``chunk_id`` is the chunk being moved; ``after_chunk_id=null`` moves it to the very
    top of the ready queue, otherwise it lands immediately after the named chunk. Both
    must name currently-ready chunks (``409`` otherwise), and ``after_chunk_id`` must
    not equal ``chunk_id`` (``422``) — a chunk cannot anchor against itself.
    """

    chunk_id: str
    after_chunk_id: str | None


class ChunkGroupRequest(BaseModel):
    """Merge unacquired chunks into one.

    ``merge_chunk_ids`` are the ready chunks folded into the path's survivor chunk; the
    survivor absorbs the union of their work refs and the merged chunks are discarded as
    ephemeral. Self-references and duplicates are ignored; a non-ready member is
    rejected ``409``.
    """

    merge_chunk_ids: list[str]


class ChunkGroupResponse(BaseModel):
    """The survivor chunk after a group — its id and the union of work refs it carries."""

    chunk_id: str
    work_refs: list[WorkRefModel] = []
    merged_chunk_ids: list[str] = []
