"""Work-source item routes (blizzard#358) — the operator-plane editor surface over a
work source's browsable items, distinct from the pass-through ``WorkItemEntry``
(``wire/chunk.py``) every source answers for a chunk's held pointers.

Every request model is ``extra="forbid"`` (mirrors ``wire/sse.py``): a client-supplied
``author`` on create is rejected by validation, never silently ignored. The patch model
follows ``ChunkPatchRequest``'s omitted-versus-explicit-null convention for
``stated_priority`` — a nullable field, so "leave unchanged" (omitted) must stay
distinguishable from "clear it" (explicit ``null``) by the key's presence in the body."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

#: The three stated-priority values a create/edit may set — validated here, on the way
#: in; the column itself is an unconstrained ``str | None`` (``hub/store/schema.py``).
WorkItemPriority = Literal["low", "normal", "high"]


class WorkSourceSummary(BaseModel):
    """One work source's capability booleans — the ``GET /api/work-sources`` listing
    row. ``readable`` is not a field: every source answers ``fetch``, so it carries no
    information; ``edit`` is the "has browsable items" signal the item routes gate on."""

    name: str
    annotate: bool
    close: bool
    edit: bool


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
    stated_priority: str | None
    created_at: str
    edited_at: str
    closed_at: str | None
    closure: str | None


class WorkItemsListView(BaseModel):
    """A source's items, newest first — ``GET /api/work-sources/{source}/items``."""

    items: list[WorkItemView] = []


class WorkItemCreateRequest(BaseModel):
    """``POST /api/work-sources/{source}/items`` — ``author`` is stamped from the
    caller's resolved identity, never accepted here."""

    model_config = ConfigDict(extra="forbid")

    title: str
    body: str
    stated_priority: WorkItemPriority = "normal"


class WorkItemPatchRequest(BaseModel):
    """``PATCH /api/work-sources/{source}/items/{ref}`` — every field independently
    optional and applied all-or-nothing. ``title``/``body`` mean "leave unchanged"
    whether omitted or explicitly ``null`` (neither is nullable in the domain);
    ``stated_priority`` *is* nullable, so omitted stays distinguishable from explicit
    ``null`` by the key's presence in the body, checked via ``model_fields_set``."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    body: str | None = None
    stated_priority: WorkItemPriority | None = None
