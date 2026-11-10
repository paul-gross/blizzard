"""Routine create, edit, lifecycle, and run requests, the routine document, and the routine,
baseline, and run read views."""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict

from blizzard.foundation.run_mode import RunMode
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.wire.base import WireModel
from blizzard.wire.problem import ProblemDetails
from blizzard.wire.vocabulary import OPEN


class RoutineCreateRequest(BaseModel):
    """``slug`` is the routine's lineage and handle, ``name`` its display name; at least one is stated, and with
    one missing the request resolves to the same value for both. A request stating only ``name`` is deprecated and
    will be removed in 0.6; state ``slug``."""

    slug: str | None = None
    name: str | None = None
    graph_name: str
    default_scope_slug: str
    default_model: list[str] = []
    default_effort: str | None = None
    # The routine's default harness preference — the `default_model` shape.
    default_harnesses: list[str] = []
    #: A run's urgency when nothing above names one, by id or slug; ``None`` takes the tenant's default.
    default_urgency: str | None = None


class RoutineDocument(BaseModel):
    """A routine as a document entry, keyed by its immutable ``slug`` (or, in the older form, its ``name``, deprecated
    and removed in 0.6) — the model whose JSON Schema ``GET /api/config/schema/routines`` serves. ``scopes`` is the
    linked scope set, the default scope always among it; an entry that omits it leaves the stored set. Every scope an
    entry names must be stored or declared in the same document, and ``graph_name`` must name an enabled graph."""

    model_config = ConfigDict(extra="forbid")

    slug: str | None = None
    name: str | None = None
    graph_name: str
    default_scope_slug: str
    default_model: list[str] = []
    default_effort: str | None = None
    default_harnesses: list[str] = []
    #: A run's live urgency by slug, stored or declared alongside; ``None`` takes the tenant's default.
    default_urgency: str | None = None
    scopes: list[str] = []


class RoutineEditRequest(BaseModel):
    """A sparse edit: an absent field is unchanged, a present one is set. A present
    ``slug`` may only restate the current one; ``name`` sets the display name. An explicit ``null`` clears
    ``default_effort`` or ``default_urgency`` and is refused on every other field."""

    model_config = ConfigDict(extra="forbid")

    slug: str | None = None
    name: str | None = None
    graph_name: str | None = None
    default_scope_slug: str | None = None
    default_model: list[str] | None = None
    default_effort: str | None = None
    default_harnesses: list[str] | None = None
    #: The urgency a run takes when nothing above the routine names one, by id or slug.
    default_urgency: str | None = None


class RoutineLifecycleRequest(BaseModel):
    """A request to retire or re-enable a routine; ``by`` names who the change is attributed to."""

    by: str = "operator"


class RoutineView(WireModel):
    """A routine as served by the create/list/read/edit/lifecycle routes."""

    routine_id: str
    #: The routine's lineage and handle; immutable.
    slug: str
    #: The routine's editable display name.
    name: str
    graph_name: str
    default_scope_slug: str
    default_model: list[str] = []
    default_effort: str | None = None
    default_harnesses: list[str] = []
    #: The slug of a run's urgency when nothing above names one; ``None`` takes the tenant's default.
    default_urgency: str | None = None
    created_at: str
    retired: bool = False
    revision: int | None = None
    #: The id of the project this belongs to; ``None`` where the hub reports none.
    project_id: str | None = None
    #: The project's current slug; ``None`` where the hub reports none.
    project_slug: str | None = None
    #: The project's current display name; ``None`` where the hub reports none.
    project_name: str | None = None


class RoutineRunRequest(BaseModel):
    """``POST /api/routines/{routine_id}/run`` — ``scope_slug`` omitted
    or ``None`` defaults to the routine's own; ``mode`` is ``"full"`` or ``"delta"``, a
    requested ``"delta"`` with no recorded baseline downgrading to ``"full"`` on the
    response rather than refusing."""

    model_config = ConfigDict(extra="forbid")

    scope_slug: str | None = None
    mode: RunMode = RunMode.FULL
    note: str | None = None
    #: This run's urgency by id or slug, over the routine's default; ``None`` falls down the chain.
    urgency: str | None = None


class RoutineBaselineRepoView(BaseModel):
    """One repo's recorded baseline revision and how much has landed against it since
    — ``GET /api/routines/{routine_id}/baselines``."""

    repo: str
    revision: str
    landed_since: int


class RoutineBaselineView(BaseModel):
    """One scope a routine has swept; a scope it never swept has no row."""

    scope_slug: str
    finding_set_id: str
    recorded_at: str
    repos: list[RoutineBaselineRepoView]


class RoutineRunResponse(WireModel):
    """The minted and ingested run item — the chunk id, the item's own
    pointer, the effective mode and whether it was downgraded from a requested delta,
    and the resolved baseline, when the mode settled on delta."""

    chunk_id: str
    source: str
    ref: str
    title: str
    body: str
    #: The routine's slug.
    routine_name: str
    scope_slug: str
    effective_mode: Annotated[RunMode | Unrecognized, OPEN]
    downgraded: bool
    baseline_finding_set_id: str | None = None
    baseline_revisions: dict[str, str] | None = None
    created_at: str


ROUTINE_NOT_FOUND: Final = "urn:blizzard:problem:routine-not-found"


ROUTINE_DEFINITION_INVALID: Final = "urn:blizzard:problem:routine-definition-invalid"


ROUTINE_SCOPE_NOT_RELATED: Final = "urn:blizzard:problem:routine-scope-not-related"


ROUTINE_NOT_RUNNABLE: Final = "urn:blizzard:problem:routine-not-runnable"


class RoutineNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:routine-not-found"]]):
    """The named routine does not exist in the project (404 on a read or write, 422 when a body names it)."""


class RoutineDefinitionInvalidProblem(ProblemDetails[Literal["urn:blizzard:problem:routine-definition-invalid"]]):
    """A routine write states a definition that cannot hold: a bad or taken slug, an unresolved graph, a scope of
    another project, invalid harnesses, an immutable slug changed, or the default scope unlinked."""


class RoutineScopeNotRelatedProblem(ProblemDetails[Literal["urn:blizzard:problem:routine-scope-not-related"]]):
    """A run names a scope outside the routine's related set."""


class RoutineNotRunnableProblem(ProblemDetails[Literal["urn:blizzard:problem:routine-not-runnable"]]):
    """A routine run is refused: the routine or the effective scope is retired, or the graph has no enabled mint."""


ROUTINE_PROBLEM_TYPES: frozenset[str] = frozenset(
    {ROUTINE_NOT_FOUND, ROUTINE_DEFINITION_INVALID, ROUTINE_SCOPE_NOT_RELATED, ROUTINE_NOT_RUNNABLE}
)
