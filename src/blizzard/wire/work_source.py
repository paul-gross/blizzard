"""Work-source item routes (blizzard#358) — the operator-plane editor surface over a
work source's browsable items, distinct from the pass-through ``WorkItemEntry``
(``wire/chunk.py``). Every request model is ``extra="forbid"`` (mirrors ``wire/sse.py``);
the patch model follows ``ChunkPatchRequest``'s omitted-versus-explicit-null convention
for the nullable ``stated_priority``. ``stated_priority``/``closure`` type directly on
the domain's own enums (``wire/chunk.py``'s ``status: ChunkStatus`` precedent), request
and response alike, rather than a wire-local duplicate of the same vocabulary."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from blizzard.hub.domain.work import WorkItemClosure, WorkItemPriority


class WorkSourceSummary(BaseModel):
    """One work source's capability booleans — the ``GET /api/work-sources`` listing
    row. ``readable`` is not a field: every source answers ``fetch``, so it carries no
    information; ``edit`` is the "has browsable items" signal the item routes gate on."""

    name: str
    annotate: bool
    close: bool
    edit: bool


class WorkSourcesListView(BaseModel):
    """Every configured (plus the built-in ``hub``) source — ``GET /api/work-sources``.
    Wrapped, not a bare array, so a future field can join it non-breakingly
    (``docs/versioning.md``), matching ``WorkItemsListView`` beside it."""

    sources: list[WorkSourceSummary] = []


class WorkItemAuthorView(BaseModel):
    """Who filed a hub-owned work item — ``user_id`` set only for ``kind == "user"``."""

    kind: str
    user_id: str | None = None


class WorkItemView(BaseModel):
    """One hub-owned work item in full — author, stated priority, closure, and the
    last-edit instant, the vocabulary a pass-through ``WorkItemEntry`` cannot answer."""

    source: str
    ref: str
    label: str | None
    web_url: str | None
    title: str
    body: str
    author: WorkItemAuthorView
    stated_priority: WorkItemPriority | None
    created_at: str
    edited_at: str
    closed_at: str | None
    closure: WorkItemClosure | None


class WorkItemsListView(BaseModel):
    """A source's items, newest first — ``GET /api/work-sources/{source}/items``."""

    items: list[WorkItemView] = []


class WorkItemCreateRequest(BaseModel):
    """``POST /api/work-sources/{source}/items`` — ``author`` is stamped from the
    caller's resolved identity, never accepted here."""

    model_config = ConfigDict(extra="forbid")

    title: str
    body: str
    stated_priority: WorkItemPriority = WorkItemPriority.NORMAL


class WorkItemPatchRequest(BaseModel):
    """``PATCH /api/work-sources/{source}/items/{ref}`` — every field optional, applied
    all-or-nothing. ``stated_priority`` is nullable, so omitted (unchanged) must stay
    distinguishable from explicit ``null`` (cleared) via ``model_fields_set``."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    body: str | None = None
    stated_priority: WorkItemPriority | None = None
