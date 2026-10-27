"""The ready-queue peek and the backlog's own reordering surface.

``GET /api/queue`` (and the runner's ``GET /api/fleet/queue/peek``) returns the hub-ordered
ready queue, read-only; ``GET /api/backlog`` is its independently-ranked ``not_ready``
counterpart. ``POST /api/fleet/queue/peek`` is a second verb on the same path — the
matched peek, one entry for the calling principal, sharing the ``GET``'s response model."""

from __future__ import annotations

from pydantic import BaseModel, model_validator

from blizzard.wire.base import WireModel
from blizzard.wire.chunk import BlockedView, WorkRefModel
from blizzard.wire.runner import RunnerCapability


class QueuePeekEntry(WireModel):
    """One ready chunk, in queue order."""

    chunk_id: str
    graph_id: str
    position: int
    work_refs: list[WorkRefModel] = []
    # The chunk's blocked marking — see BlockedView.
    blocked: BlockedView | None = None
    #: The id of the project this belongs to; ``None`` where the hub reports none.
    project_id: str | None = None
    #: The project's current slug; ``None`` where the hub reports none.
    project_slug: str | None = None
    #: The project's current display name; ``None`` where the hub reports none.
    project_name: str | None = None


class QueuePeekResponse(WireModel):
    """The ready queue's whole order, unpaginated."""

    entries: list[QueuePeekEntry] = []


class QueuePeekRequest(BaseModel):
    """The matched fleet peek's own request body — ``POST /api/fleet/queue/peek``. Carries
    the calling runner's capability snapshot and queue policy; never a ``runner_id``,
    since the matched verb answers for the authenticated principal alone. ``policy="hold"``
    stops at an unusable head; any other value, including an unrecognized one, is pass-over."""

    capabilities: list[RunnerCapability] = []
    policy: str = "pass-over"
    #: Narrows the peek to this one project of the runner's declaration, by the entry as declared or the project id it
    #: resolved to; a project the runner does not serve is offered nothing. ``None`` peeks every project it serves.
    project: str | None = None
    #: Narrows the peek to these projects of the runner's declaration, each by the entry as declared or the id it
    #: resolved to, and — under ``policy="hold"`` — holds per project rather than across them. ``None`` leaves the peek
    #: to ``project``; an empty list offers nothing. Sent together with ``project`` it is refused.
    projects: list[str] | None = None

    @model_validator(mode="after")
    def _one_narrowing(self) -> QueuePeekRequest:
        if self.project is not None and self.projects is not None:
            raise ValueError("send either project or projects, not both")
        return self


class QueuePageView(WireModel):
    """``GET /api/queue``'s own bounded page: ``next_cursor`` is
    ``None`` on the last page; ``position`` is each entry's whole-order index, not page-local."""

    entries: list[QueuePeekEntry] = []
    next_cursor: str | None = None


class QueueReplaceRequest(BaseModel):
    """Idempotent whole-order replacement of the ready queue — ``PUT /api/queue``.

    ``chunk_ids`` is the desired order, front to back; each must name a ready chunk
    (``409``) and not repeat (``422``). An unnamed ready chunk is appended, order kept."""

    chunk_ids: list[str]


class QueuePositionRequest(BaseModel):
    """Single-chunk fractional reposition — ``POST /api/queue/position``.

    ``after_chunk_id=null`` moves ``chunk_id`` to the top, otherwise immediately after
    the named chunk. Both must be ready (``409``); a self-anchor is ``422``."""

    chunk_id: str
    after_chunk_id: str | None


class BacklogPeekEntry(WireModel):
    """One ``not_ready`` chunk, in backlog order."""

    chunk_id: str
    graph_id: str
    position: int
    work_refs: list[WorkRefModel] = []
    # The chunk's blocked marking — see BlockedView.
    blocked: BlockedView | None = None
    #: The id of the project this belongs to; ``None`` where the hub reports none.
    project_id: str | None = None
    #: The project's current slug; ``None`` where the hub reports none.
    project_slug: str | None = None
    #: The project's current display name; ``None`` where the hub reports none.
    project_name: str | None = None


class BacklogPeekResponse(WireModel):
    """The ``not_ready`` list's whole order, unpaginated."""

    entries: list[BacklogPeekEntry] = []


class BacklogPageView(WireModel):
    """``GET /api/backlog``'s own bounded page: ``next_cursor``
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


class ChunkGroupResponse(WireModel):
    """The survivor chunk after a group — its id and the union of work refs it carries."""

    chunk_id: str
    work_refs: list[WorkRefModel] = []
    merged_chunk_ids: list[str] = []
