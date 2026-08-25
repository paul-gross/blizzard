"""The ready-queue peek and the backlog's own reordering surface.

``GET /api/queue`` (and the runner's fleet-side ``GET /api/fleet/queue/peek``) returns
the hub-ordered ready queue, read-only. ``GET /api/backlog`` is its ``not_ready``-list
counterpart, ranked independently (``bzh:ranking-is-per-list``); their wire models are
kept separate rather than shared. Order derives from appended facts."""

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

    ``chunk_ids`` is the desired order, front to back; each must name a ready chunk
    (``409``) and not repeat (``422``). An unnamed ready chunk is appended, order kept."""

    chunk_ids: list[str]


class QueuePositionRequest(BaseModel):
    """Single-chunk fractional reposition — ``POST /api/queue/position`` (issue #137).

    ``after_chunk_id=null`` moves ``chunk_id`` to the top, otherwise immediately after
    the named chunk. Both must be ready (``409``); a self-anchor is ``422``."""

    chunk_id: str
    after_chunk_id: str | None


class BacklogPeekEntry(BaseModel):
    """One ``not_ready`` chunk, in backlog order."""

    chunk_id: str
    graph_id: str
    position: int
    work_refs: list[WorkRefModel] = []


class BacklogPeekResponse(BaseModel):
    """The ``not_ready`` list, in the hub's explicit order."""

    entries: list[BacklogPeekEntry] = []


class BacklogReplaceRequest(BaseModel):
    """Idempotent whole-order replacement of the backlog — ``PUT /api/backlog``.

    ``chunk_ids`` is the desired order, front to back; each must name a ``not_ready``
    chunk (``409``) and not repeat (``422``). An unnamed chunk is appended, order kept."""

    chunk_ids: list[str]


class BacklogPositionRequest(BaseModel):
    """Single-chunk fractional reposition — ``POST /api/backlog/position``.

    ``after_chunk_id=null`` moves ``chunk_id`` to the top, otherwise immediately after
    the named chunk. Both must be ``not_ready`` (``409``); a self-anchor is ``422``."""

    chunk_id: str
    after_chunk_id: str | None


class ChunkGroupRequest(BaseModel):
    """Merge unacquired chunks into one.

    ``merge_chunk_ids`` fold into the path's survivor, which absorbs the union of their
    work refs. Self-references and duplicates are ignored; a non-ready member is ``409``."""

    merge_chunk_ids: list[str]


class ChunkGroupResponse(BaseModel):
    """The survivor chunk after a group — its id and the union of work refs it carries."""

    chunk_id: str
    work_refs: list[WorkRefModel] = []
    merged_chunk_ids: list[str] = []
