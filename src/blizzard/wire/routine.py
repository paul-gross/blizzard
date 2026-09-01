"""Routine create/edit/run requests and their read views (issue #389, blizzard#392).

A create names the graph its runs execute and a default scope (minted if unseen, D4);
edit changes everything but the name, which is immutable (D7). A run mints, ingests, and
promotes a hub work item from the routine in one act."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class RoutineCreateRequest(BaseModel):
    name: str
    graph_name: str
    default_scope_slug: str
    default_model: list[str] = []
    default_effort: str | None = None


class RoutineEditRequest(BaseModel):
    """``name`` is required and must equal the routine's current one (D7) — the request
    restates it so a caller cannot silently target the wrong routine's edit."""

    name: str
    graph_name: str
    default_scope_slug: str
    default_model: list[str] = []
    default_effort: str | None = None


class RoutineView(BaseModel):
    """A routine as served by the create/list/read/edit routes."""

    routine_id: str
    name: str
    graph_name: str
    default_scope_slug: str
    default_model: list[str] = []
    default_effort: str | None = None
    created_at: str


class RoutineRunRequest(BaseModel):
    """``POST /api/routines/{routine_id}/run`` (blizzard#392) — ``scope_slug`` omitted
    or ``None`` defaults to the routine's own; ``mode`` is ``"full"`` or ``"delta"``, a
    requested ``"delta"`` with no recorded baseline downgrading to ``"full"`` on the
    response rather than refusing."""

    model_config = ConfigDict(extra="forbid")

    scope_slug: str | None = None
    mode: str = "full"
    note: str | None = None


class RoutineBaselineRepoView(BaseModel):
    """One repo's recorded baseline revision and how much has landed against it since
    (D1) — ``GET /api/routines/{routine_id}/baselines``."""

    repo: str
    revision: str
    landed_since: int


class RoutineBaselineView(BaseModel):
    """One scope a routine has swept (D5) — absence from the list this route serves
    means the routine has never swept that scope."""

    scope_slug: str
    finding_set_id: str
    recorded_at: str
    repos: list[RoutineBaselineRepoView]


class RoutineRunResponse(BaseModel):
    """The minted, ingested, and promoted run item — the chunk id, the item's own
    pointer, the effective mode and whether it was downgraded from a requested delta,
    and the resolved baseline, when the mode settled on delta."""

    chunk_id: str
    source: str
    ref: str
    title: str
    body: str
    routine_name: str
    scope_slug: str
    effective_mode: str
    downgraded: bool
    baseline_finding_set_id: str | None = None
    baseline_revisions: dict[str, str] | None = None
    created_at: str
