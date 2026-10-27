"""Work-source wire shapes: the source summary, document, patch request, and list; the hub-owned work item, its
author, and its list; and the item create and patch requests and create response."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from blizzard.foundation.unrecognized import Unrecognized
from blizzard.foundation.work_items import WorkItemClosure, WorkItemPriority
from blizzard.wire.base import WireModel
from blizzard.wire.forge import DEPRECATED_FIELD, OldFieldWarning
from blizzard.wire.vocabulary import OPEN


class WorkSourceSummary(WireModel):
    """One work source. ``edit`` is whether the source has browsable items.
    ``work_source_id`` is what every stored reference holds; ``slug`` is the handle people type, and ``name`` a display
    label. The built-in ``hub`` source is ``built_in``, has slug and name ``hub``, and carries no id or record
    fields."""

    work_source_id: str | None = None
    slug: str | None = None
    name: str
    annotate: bool
    edit: bool
    provider: str | None = None
    locator: str | None = None
    forge_id: str | None = None
    forge_slug: str | None = None
    forge_name: str | None = None
    #: The API origin of the forge the source names — read ``forge_id`` instead.
    api_base: str | None = Field(default=None, json_schema_extra=DEPRECATED_FIELD)
    #: The web origin of the forge the source names — read ``forge_id`` instead.
    web_base: str | None = Field(default=None, json_schema_extra=DEPRECATED_FIELD)
    secret: str | None = None
    revision: int | None = None
    created_at: str | None = None
    created_by: str | None = None
    created_by_name: str | None = None
    retired: bool = False
    built_in: bool = False
    #: One entry per old address field the write that answered this used — empty on every other answer.
    warnings: list[OldFieldWarning] = []


class WorkSourceDocument(BaseModel):
    """A work source as a document — an entry of the version-1 configuration document, and the model whose JSON Schema
    ``GET /api/config/schema/work-sources`` serves. ``slug`` names the source and ``name`` is its display label — the
    slug when omitted on create, left as stored on an edit. ``hub`` is never a slug: the built-in source holds it.
    ``secret`` names a stored secret."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str | None = None
    provider: str
    locator: str
    api_base: str | None = None
    web_base: str | None = None
    annotate: bool = False
    secret: str | None = None


class WorkSourceCreateRequest(BaseModel):
    """The ``POST /api/work-sources`` body: every field of a work source's document entry, with ``forge`` — a forge's id
    or slug — beside them. ``api_base`` and ``web_base`` are the old way to name it, resolved to the tenant's forge with
    that API origin and answered with a warning; a source naming neither ``forge`` nor ``api_base`` is on GitHub. The
    web origin belongs to the forge: a ``web_base`` other than the forge's is refused, and a blank one means the
    forge's."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str | None = None
    provider: str
    locator: str
    forge: str | None = None
    api_base: str | None = Field(default=None, json_schema_extra=DEPRECATED_FIELD)
    web_base: str | None = Field(default=None, json_schema_extra=DEPRECATED_FIELD)
    annotate: bool = False
    secret: str | None = None


class WorkSourcePatchRequest(BaseModel):
    """A sparse edit: an absent field is unchanged, a present one is set, and an explicit ``null`` is refused on every
    field but ``api_base``, ``web_base``, and ``secret``. ``forge`` re-points the source at a forge by id or slug;
    ``api_base`` does so the old way, with a warning, and a null one means GitHub's. ``web_base`` changes nothing: one
    other than the forge's web origin is refused, and a null one, the forge's, is accepted with a warning. A null
    ``secret`` clears it. A slug that changes keeps resolving to the source until another claims it."""

    model_config = ConfigDict(extra="forbid")

    slug: str | None = None
    name: str | None = None
    provider: str | None = None
    locator: str | None = None
    forge: str | None = None
    api_base: str | None = Field(default=None, json_schema_extra=DEPRECATED_FIELD)
    web_base: str | None = Field(default=None, json_schema_extra=DEPRECATED_FIELD)
    annotate: bool | None = None
    secret: str | None = None


class WorkSourcesListView(WireModel):
    """Every configured (plus the built-in ``hub``) source — ``GET /api/work-sources``."""

    sources: list[WorkSourceSummary] = []


class WorkItemAuthorView(WireModel):
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


class WorkItemView(WireModel):
    """One hub-owned work item in full — author, stated priority, closure, and the
    last-edit instant, the vocabulary a pass-through ``WorkItemEntry`` cannot answer. ``source`` is the source's key —
    ``hub`` or its id — and ``slug`` its current slug; ``label`` is the rendered ``{slug}#{ref}``."""

    source: str
    slug: str | None = None
    ref: str
    label: str | None
    web_url: str | None
    title: str
    body: str
    author: WorkItemAuthorView
    stated_priority: Annotated[WorkItemPriority | Unrecognized, OPEN] | None
    created_at: str
    edited_at: str
    closed_at: str | None
    closure: Annotated[WorkItemClosure | Unrecognized, OPEN] | None


class WorkItemsListView(WireModel):
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
