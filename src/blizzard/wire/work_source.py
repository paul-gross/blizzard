"""Work-source item routes — the operator-plane editor surface over a
work source's browsable items, distinct from the pass-through ``WorkItemEntry``
(``src/blizzard/wire/chunk.py``). Every request model is ``extra="forbid"`` (mirrors ``src/blizzard/wire/sse.py``);
the patch model follows ``ChunkPatchRequest``'s omitted-versus-explicit-null convention
for the nullable ``stated_priority``, and ``stated_priority``/``closure`` type on the
domain's own enums, request and response alike (``status: ChunkStatus`` precedent)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from blizzard.foundation.work_items import WorkItemClosure, WorkItemPriority


class WorkSourceSummary(BaseModel):
    """One work source — the ``GET /api/work-sources`` listing row and every record verb's
    view. ``readable`` is not a field: every source answers ``fetch``, so it carries no
    information; ``edit`` is the "has browsable items" signal the item routes gate on. The
    built-in ``hub`` source is ``built_in`` and carries no record fields."""

    name: str
    annotate: bool
    edit: bool
    provider: str | None = None
    locator: str | None = None
    api_base: str | None = None
    web_base: str | None = None
    secret: str | None = None
    revision: int | None = None
    created_at: str | None = None
    created_by: str | None = None
    retired: bool = False
    built_in: bool = False


class WorkSourceDocument(BaseModel):
    """A work source as a document — the create body, and the model whose JSON Schema
    ``GET /api/config/schema/work-sources`` serves. ``secret`` names a stored secret."""

    model_config = ConfigDict(extra="forbid")

    name: str
    provider: str
    locator: str
    api_base: str | None = None
    web_base: str | None = None
    annotate: bool = False
    secret: str | None = None


class WorkSourcePatchRequest(BaseModel):
    """A sparse edit: an absent field is unchanged, a present one is set, and an explicit
    ``null`` clears ``api_base``, ``web_base``, or ``secret`` and is refused elsewhere.
    ``name`` is immutable, so a body carrying it is refused."""

    model_config = ConfigDict(extra="forbid")

    provider: str | None = None
    locator: str | None = None
    api_base: str | None = None
    web_base: str | None = None
    annotate: bool | None = None
    secret: str | None = None


class WorkSourcesListView(BaseModel):
    """Every configured (plus the built-in ``hub``) source — ``GET /api/work-sources``."""

    sources: list[WorkSourceSummary] = []


class WorkItemAuthorView(BaseModel):
    """Who filed a hub-owned work item, legible for display — ``user_id``
    and ``login`` set only for ``kind == "user"``; ``runner_id``/``chunk_id``/``node_name``
    — the proposing runner, chunk, and node — set only for ``kind == "fleet"``, with
    ``runner_name`` the proposing runner's latest registered name when the registry holds it."""

    kind: str
    user_id: str | None = None
    login: str | None = None
    runner_id: str | None = None
    runner_name: str | None = None
    chunk_id: str | None = None
    node_name: str | None = None


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


class WorkItemCreateResponse(WorkItemView):
    """``POST /api/work-sources/{source}/items`` — carries every
    ``WorkItemView`` field plus ``chunk_id``, the id of the ``not_ready`` chunk
    creation mints in the same transaction."""

    chunk_id: str


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
