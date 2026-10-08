"""Routine create/edit/run requests and their read views.

A create names the graph its runs execute and a default scope (minted if unseen);
edit is sparse and changes everything but the name, which is immutable. A run mints and ingests a
hub work item from the routine in one act; its chunk rests ``not_ready`` until promoted."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from blizzard.foundation.run_mode import RunMode


class RoutineCreateRequest(BaseModel):
    name: str
    graph_name: str
    default_scope_slug: str
    default_model: list[str] = []
    default_effort: str | None = None
    # The routine's default harness preference — the `default_model` shape.
    default_harnesses: list[str] = []


class RoutineDocument(BaseModel):
    """A routine as a document entry, keyed by its immutable ``name`` — the model whose JSON Schema
    ``GET /api/config/schema/routines`` serves. ``scopes`` is the linked scope set, the default scope
    always among it; an entry that omits it leaves the stored set. Every scope an entry names must be
    stored or declared in the same document, and ``graph_name`` must name an enabled graph."""

    model_config = ConfigDict(extra="forbid")

    name: str
    graph_name: str
    default_scope_slug: str
    default_model: list[str] = []
    default_effort: str | None = None
    default_harnesses: list[str] = []
    scopes: list[str] = []


class RoutineEditRequest(BaseModel):
    """A sparse edit: an absent field is unchanged, a present one is set. A present
    ``name`` must equal the routine's current one; an explicit ``null`` clears
    ``default_effort`` and is refused on every other field."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    graph_name: str | None = None
    default_scope_slug: str | None = None
    default_model: list[str] | None = None
    default_effort: str | None = None
    default_harnesses: list[str] | None = None


class RoutineLifecycleRequest(BaseModel):
    """Retire or re-enable a routine — ``by`` is recorded on the lifecycle fact; the change
    row's actor is the authenticated caller."""

    by: str = "operator"


class RoutineView(BaseModel):
    """A routine as served by the create/list/read/edit/lifecycle routes."""

    routine_id: str
    name: str
    graph_name: str
    default_scope_slug: str
    default_model: list[str] = []
    default_effort: str | None = None
    default_harnesses: list[str] = []
    created_at: str
    retired: bool = False
    revision: int | None = None


class RoutineRunRequest(BaseModel):
    """``POST /api/routines/{routine_id}/run`` — ``scope_slug`` omitted
    or ``None`` defaults to the routine's own; ``mode`` is ``"full"`` or ``"delta"``, a
    requested ``"delta"`` with no recorded baseline downgrading to ``"full"`` on the
    response rather than refusing."""

    model_config = ConfigDict(extra="forbid")

    scope_slug: str | None = None
    mode: RunMode = RunMode.FULL
    note: str | None = None


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


class RoutineRunResponse(BaseModel):
    """The minted and ingested run item — the chunk id, the item's own
    pointer, the effective mode and whether it was downgraded from a requested delta,
    and the resolved baseline, when the mode settled on delta."""

    chunk_id: str
    source: str
    ref: str
    title: str
    body: str
    routine_name: str
    scope_slug: str
    effective_mode: RunMode
    downgraded: bool
    baseline_finding_set_id: str | None = None
    baseline_revisions: dict[str, str] | None = None
    created_at: str
