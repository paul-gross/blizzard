"""The ready-queue peek and the backlog's own reordering surface.

``GET /api/queue`` (and the runner's ``GET /api/fleet/queue/peek``) returns the hub-ordered
ready queue, read-only; ``GET /api/backlog`` is its independently-ranked ``not_ready``
counterpart. ``POST /api/fleet/queue/peek`` is a second verb on the same path — the
matched peek, one entry for the calling principal, sharing the ``GET``'s response model."""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.wire.chunk import BlockedView, WorkRefModel
from blizzard.wire.runner import RunnerCapability


class QueuePeekEntry(BaseModel):
    """One ready chunk, in queue order."""

    chunk_id: str
    graph_id: str
    position: int
    work_refs: list[WorkRefModel] = []
    # The chunk's blocked marking (issue #457) — see BlockedView.
    blocked: BlockedView | None = None


class QueuePeekResponse(BaseModel):
    """The ready queue's whole order, unpaginated — a write verb's caller needs it in
    full to confirm against, not one page (blizzard#526 D3)."""

    entries: list[QueuePeekEntry] = []


class QueuePeekRequest(BaseModel):
    """The matched fleet peek's own request body — ``POST /api/fleet/queue/peek``. Carries
    the calling runner's capability snapshot and queue policy; never a ``runner_id``,
    since the matched verb answers for the authenticated principal alone. ``policy``'s
    semantics: :class:`~blizzard.hub.domain.queue.QueueMatchPolicy`."""

    capabilities: list[RunnerCapability] = []
    policy: str = "pass-over"


class QueuePageView(BaseModel):
    """``GET /api/queue``'s own bounded page (blizzard#526 D3/D4): ``next_cursor`` is
    ``None`` on the last page; ``position`` is each entry's whole-order index, not page-local."""

    entries: list[QueuePeekEntry] = []
    next_cursor: str | None = None


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
    # The chunk's blocked marking (issue #457) — see BlockedView.
    blocked: BlockedView | None = None


class BacklogPeekResponse(BaseModel):
    """The ``not_ready`` list's whole order, unpaginated — a write verb's caller needs
    it in full to confirm against, not one page (blizzard#526 D3)."""

    entries: list[BacklogPeekEntry] = []


class BacklogPageView(BaseModel):
    """``GET /api/backlog``'s own bounded page (blizzard#526 D3/D4): ``next_cursor``
    is ``None`` on the last page; ``position`` is each entry's whole-order index, not page-local."""

    entries: list[BacklogPeekEntry] = []
    next_cursor: str | None = None


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
