"""The ready-queue peek and the backlog's own reordering surface.

``GET /api/queue`` (and the runner's fleet-side ``GET /api/fleet/queue/peek``) returns
the hub-ordered ready queue, read-only. ``GET /api/backlog`` is its ``not_ready``-list
counterpart, ranked independently (``bzh:ranking-is-per-list``); their wire models are
kept separate rather than shared. Order derives from appended facts.

``POST /api/fleet/queue/peek`` (blizzard#433 Phase 3) is a second verb on the same path
— the matched fleet peek, answering at most one entry for the calling principal alone;
the response model it shares with the ``GET`` above stays unchanged."""

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
    """The matched fleet peek's own request body — ``POST /api/fleet/queue/peek``
    (blizzard#433 Phase 3, D7). Carries the calling runner's own capability snapshot and
    its queue policy; never a ``runner_id`` — the matched verb answers for the
    authenticated principal alone.

    ``capabilities`` empty (a runner asserting none) applies no capability filter — only
    the blocked-dependency dimension applies, matching the unfiltered reach-ahead the
    legacy ``GET`` already gives the head entry. ``policy`` is an open string
    (``docs/versioning.md``'s round-trip-the-unrecognized rule, D8): ``"hold"`` stops at
    an unusable head and yields no entry; anything else, including a value this hub does
    not recognize, reads as ``"pass-over"``, the default."""

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
