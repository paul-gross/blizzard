"""Work-source wire shapes: the source summary, document, patch request, and list; the hub-owned work item, its
author, and its list; and the item create and patch requests and create response."""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from blizzard.foundation.unrecognized import Unrecognized
from blizzard.foundation.work_items import WorkItemClosure, WorkItemPriority
from blizzard.wire.base import WireModel
from blizzard.wire.forge import DEPRECATED_FIELD, MovedFieldsRequest, OldFieldWarning
from blizzard.wire.problem import CommonProblemMembers, ProblemDetails
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
    secret: str | None = None
    revision: int | None = None
    created_at: str | None = None
    created_by: str | None = None
    created_by_name: str | None = None
    retired: bool = False
    built_in: bool = False
    #: Always empty.
    warnings: list[OldFieldWarning] = Field(default=[], json_schema_extra=DEPRECATED_FIELD)


#: The address fields a work source's forge now carries.
WORK_SOURCE_MOVED_FIELDS = ("api_base", "web_base")


class WorkSourceCreateRequest(MovedFieldsRequest):
    """The ``POST /api/work-sources`` body: every field of a work source's document entry, with ``forge`` — a forge's id
    or slug — naming the forge it is on. A body naming no forge is refused."""

    moved_fields = WORK_SOURCE_MOVED_FIELDS

    slug: str
    name: str | None = None
    provider: str
    locator: str
    forge: str | None = None
    annotate: bool = False
    secret: str | None = None


class WorkSourcePatchRequest(MovedFieldsRequest):
    """A sparse edit: an absent field is unchanged, a present one is set, and an explicit ``null`` is refused on every
    field but ``secret``, which it clears. ``forge`` re-points the source at a forge by id or slug. A slug that changes
    keeps resolving to the source until another claims it."""

    moved_fields = WORK_SOURCE_MOVED_FIELDS

    slug: str | None = None
    name: str | None = None
    provider: str | None = None
    locator: str | None = None
    forge: str | None = None
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
    #: The urgency the item's chunk is minted with, by id or slug; ``None`` takes the tenant's default.
    urgency: str | None = None


class WorkItemPatchRequest(BaseModel):
    """``PATCH /api/work-sources/{source}/items/{ref}`` — every field optional, applied
    all-or-nothing. ``stated_priority`` is nullable, so omitted (unchanged) must stay
    distinguishable from explicit ``null`` (cleared) via ``model_fields_set``."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    body: str | None = None
    stated_priority: WorkItemPriority | None = None


WORK_SOURCE_NOT_FOUND: Final = "urn:blizzard:problem:work-source-not-found"


WORK_ITEM_NOT_FOUND: Final = "urn:blizzard:problem:work-item-not-found"


WORK_SOURCE_SLUG_TAKEN: Final = "urn:blizzard:problem:work-source-slug-taken"


WORK_SOURCE_LOCATOR_TAKEN: Final = "urn:blizzard:problem:work-source-locator-taken"


WORK_SOURCE_NO_EDITOR: Final = "urn:blizzard:problem:work-source-no-editor"


WORK_ITEM_NOT_EDITABLE: Final = "urn:blizzard:problem:work-item-not-editable"


WORK_ITEM_HELD_BY_LIVE_CHUNK: Final = "urn:blizzard:problem:work-item-held-by-live-chunk"


WORK_ITEM_HELD_BY_DEPENDENTS: Final = "urn:blizzard:problem:work-item-held-by-dependents"


WORK_ITEM_FIELD_BLANK: Final = "urn:blizzard:problem:work-item-field-blank"


UNKNOWN_WORK_SOURCE: Final = "urn:blizzard:problem:unknown-work-source"


WORK_ITEMS_CHUNK_IDS_REQUIRED: Final = "urn:blizzard:problem:work-items-chunk-ids-required"


class WorkSourceNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:work-source-not-found"]]):
    """A path or request names a work source the tenant does not hold."""


class WorkItemNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:work-item-not-found"]]):
    """The work source holds no item at the named ref."""


class WorkSourceSlugTakenProblem(ProblemDetails[Literal["urn:blizzard:problem:work-source-slug-taken"]]):
    """Another work source of the tenant holds the slug."""


class WorkSourceLocatorTakenProblem(ProblemDetails[Literal["urn:blizzard:problem:work-source-locator-taken"]]):
    """Another work source holds the provider and locator."""


class WorkSourceNoEditorProblem(ProblemDetails[Literal["urn:blizzard:problem:work-source-no-editor"]]):
    """The work source offers no editing of its items."""


class WorkItemNotEditableProblem(ProblemDetails[Literal["urn:blizzard:problem:work-item-not-editable"]]):
    """The work item already carries a closure."""


class WorkItemHeldByLiveChunkProblem(ProblemDetails[Literal["urn:blizzard:problem:work-item-held-by-live-chunk"]]):
    """A live chunk still holds the work item."""


class WorkItemHeldByDependentsProblem(ProblemDetails[Literal["urn:blizzard:problem:work-item-held-by-dependents"]]):
    """The work item's holder is a standing prerequisite of other chunks."""


class WorkItemFieldBlankProblem(ProblemDetails[Literal["urn:blizzard:problem:work-item-field-blank"]]):
    """A work item's title or body is blank."""


class UnknownWorkSourceDetail(BaseModel):
    """The validation-shaped entry for an unknown work source."""

    loc: list[str]
    msg: str
    type: Literal["unknown_work_source"]


class UnknownWorkSourceProblem(
    CommonProblemMembers[Literal["urn:blizzard:problem:unknown-work-source"], list[UnknownWorkSourceDetail]]
):
    """A query names a work source the tenant does not hold."""

    status: int | None = None


class WorkItemsChunkIdsRequiredProblem(ProblemDetails[Literal["urn:blizzard:problem:work-items-chunk-ids-required"]]):
    """A bulk work-item read named no chunk."""


WORK_SOURCE_PROBLEM_TYPES: frozenset[str] = frozenset(
    {
        WORK_SOURCE_NOT_FOUND,
        WORK_ITEM_NOT_FOUND,
        WORK_SOURCE_SLUG_TAKEN,
        WORK_SOURCE_LOCATOR_TAKEN,
        WORK_SOURCE_NO_EDITOR,
        WORK_ITEM_NOT_EDITABLE,
        WORK_ITEM_HELD_BY_LIVE_CHUNK,
        WORK_ITEM_HELD_BY_DEPENDENTS,
        WORK_ITEM_FIELD_BLANK,
        UNKNOWN_WORK_SOURCE,
        WORK_ITEMS_CHUNK_IDS_REQUIRED,
    }
)
