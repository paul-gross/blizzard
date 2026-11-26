"""Scope routes — create, list, read, edit, retire, and enable a scope.

A scope's slug names it within its project, so every by-slug route and every create sits under
``/api/projects/{project}``. The un-nested ``/api/scopes`` routes stay: the listing reads the whole
tenant or the project ``?project=`` names, and each by-slug route and the create remain as deprecated
aliases acting in the call's project — the tenant's only one, else ``422 project_required``.

The controller resolves a slug into an object before delegating to the domain
(``bzh:controller-read-only``, ``bzh:domain-takes-objects``). Every write carries the
request's :class:`ChangeContext` and an optional ``If-Match`` (``bzh:configured-record``)."""

from __future__ import annotations

from functools import partial
from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject_sync
from fastapi import APIRouter, Depends, Header, HTTPException, status

from blizzard.auth_core import Permission
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api import problems, problems_garden
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.door import RequestDoor, change_context
from blizzard.hub.api.problem_exceptions import ProblemHTTPException
from blizzard.hub.api.project_label_fields import ProjectLabelFields
from blizzard.hub.api.project_labels import ProjectLabels
from blizzard.hub.api.project_params import CallProject, PathProject, QueryProject
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.domain.config.errors import ConfigFieldError, ConfigRevisionConflict
from blizzard.hub.domain.garden.routines import IReadRoutineScopeRepository
from blizzard.hub.domain.garden.scopes import (
    IReadScopeRepository,
    Scope,
    ScopeEdit,
    ScopeLifecycle,
    ScopeRegistry,
    ScopeSlug,
    ScopeSlugError,
)
from blizzard.hub.domain.projects.model import IReadProjectRepository, Project
from blizzard.wire.scope import ScopeCreateRequest, ScopeEditRequest, ScopeLifecycleRequest, ScopeView

_GUARDS = [Depends(reject_runner_principal), Depends(tenant_bound)]
router = APIRouter(prefix="/api", tags=["scopes"], dependencies=_GUARDS)
project_router = APIRouter(prefix="/api/projects/{project}", tags=["scopes"], dependencies=_GUARDS)
_VIEW = Depends(require(Permission.FLEET_VIEW))
Scopes = FromDishka[IReadScopeRepository]
Registry = FromDishka[ScopeRegistry]
Lifecycle = FromDishka[ScopeLifecycle]
RoutineScopes = FromDishka[IReadRoutineScopeRepository]
ProjectReads = FromDishka[IReadProjectRepository]


def scope_view(scope: Scope, labels: ProjectLabelFields | None = None) -> ScopeView:
    """The one `Scope` -> `ScopeView` projection, labelled with its project when ``labels`` is given."""
    view = ScopeView(
        slug=scope.slug,
        description=scope.description,
        created_at=iso_utc(scope.created_at),
        retired=scope.retired,
        revision=scope.revision,
    )
    return view if labels is None else view.model_copy(update=labels)


def _labelled(scope: Scope, project: Project) -> ScopeView:
    return scope_view(scope, ProjectLabels.of_project(project).fields(scope.project_id))


def _unprocessable(exc: ConfigFieldError) -> HTTPException:
    """A refused field, located in the body — the shape FastAPI's own validation answers."""
    return ProblemHTTPException(partial(problems_garden.config_field_invalid_problem, exc.field, exc.message))


def _revision_conflict(exc: ConfigRevisionConflict) -> HTTPException:
    return ProblemHTTPException(partial(problems.revision_conflict_problem, str(exc)))


def _scope_or_404(project: Project, slug: str, scopes: IReadScopeRepository) -> Scope:
    scope = scopes.get(project.project_id, slug)
    if scope is None:
        raise ProblemHTTPException(partial(problems_garden.scope_not_found_problem, f"unknown scope {slug}", 404))
    return scope


def _create(
    request: ScopeCreateRequest,
    identity: ResolvedIdentity,
    door: RequestDoor,
    registry: ScopeRegistry,
    project: Project,
) -> ScopeView:
    try:
        slug = ScopeSlug.parse(request.slug)
    except ScopeSlugError as exc:
        raise ProblemHTTPException(partial(problems_garden.scope_slug_invalid_problem, str(exc))) from exc
    scope = registry.ensure(project, slug, change_context(identity, door), description=request.description)
    return _labelled(scope, project)


def _edit(
    slug: str,
    request: ScopeEditRequest,
    identity: ResolvedIdentity,
    door: RequestDoor,
    registry: ScopeRegistry,
    scopes: IReadScopeRepository,
    project: Project,
    if_match: int | None,
) -> ScopeView:
    try:
        parsed = ScopeSlug.parse(slug)
    except ScopeSlugError as exc:
        raise ProblemHTTPException(partial(problems_garden.scope_slug_invalid_problem, str(exc))) from exc
    scope = _scope_or_404(project, parsed.value, scopes)
    edit = ScopeEdit(**{name: getattr(request, name) for name in request.model_fields_set})
    try:
        edited = registry.edit(scope, edit, change_context(identity, door), if_match=if_match)
    except ConfigFieldError as exc:
        raise _unprocessable(exc) from exc
    except ConfigRevisionConflict as exc:
        raise _revision_conflict(exc) from exc
    return _labelled(edited, project)


def _set_retired(
    retire: bool,
    slug: str,
    request: ScopeLifecycleRequest,
    identity: ResolvedIdentity,
    door: RequestDoor,
    lifecycle: ScopeLifecycle,
    scopes: IReadScopeRepository,
    project: Project,
    if_match: int | None,
) -> ScopeView:
    scope = _scope_or_404(project, slug, scopes)
    verb = lifecycle.retire if retire else lifecycle.enable
    try:
        changed = verb(scope, change_context(identity, door), by=identity.username, if_match=if_match)
    except ConfigRevisionConflict as exc:
        raise _revision_conflict(exc) from exc
    return _labelled(changed, project)


def _listing(scopes: IReadScopeRepository, projects: IReadProjectRepository, project_id: str | None) -> list[ScopeView]:
    listed = scopes.list_all(project_id)
    labels = ProjectLabels.read(projects, (s.project_id for s in listed))
    return [scope_view(s, labels.fields(s.project_id)) for s in listed]


# --- the un-nested routes: the tenant-wide listing, and the deprecated by-slug aliases ---


@router.post("/scopes", response_model=ScopeView, status_code=status.HTTP_201_CREATED, deprecated=True)
@inject_sync
def create_scope(
    request: ScopeCreateRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.GRAPH_EDIT))],
    door: RequestDoor,
    registry: Registry,
    project: CallProject,
) -> ScopeView:
    """Mint a scope in the call's project, or no-op onto the project's existing one of the same slug; 422 on a
    malformed slug, naming the rejected value. Deprecated: `POST /api/projects/{project}/scopes`."""
    return _create(request, identity, door, registry, project)


@router.get("/scopes", response_model=list[ScopeView], dependencies=[_VIEW])
@inject_sync
def list_scopes(scopes: Scopes, projects: ProjectReads, project: QueryProject) -> list[ScopeView]:
    """Every scope, newest first, each marked retired or not — of the project `?project=` names, else of every
    project."""
    return _listing(scopes, projects, project)


@router.get("/scopes/{slug}", response_model=ScopeView, dependencies=[_VIEW], deprecated=True)
@inject_sync
def get_scope(slug: str, scopes: Scopes, project: CallProject) -> ScopeView:
    """One scope of the call's project; 404 on an unknown slug. Deprecated: `GET
    /api/projects/{project}/scopes/{slug}`."""
    return _labelled(_scope_or_404(project, slug, scopes), project)


@router.get("/scopes/{slug}/routines", response_model=list[str], dependencies=[_VIEW], deprecated=True)
@inject_sync
def list_scope_routines(slug: str, scopes: Scopes, routine_scopes: RoutineScopes, project: CallProject) -> list[str]:
    """Every routine id linked to `slug` in the call's project — the reverse direction of
    `GET /api/routines/{routine_id}/scopes`. 404 on an unknown slug. Deprecated: `GET
    /api/projects/{project}/scopes/{slug}/routines`."""
    scope = _scope_or_404(project, slug, scopes)
    return routine_scopes.list_routines(scope.scope_id)


@router.patch("/scopes/{slug}", response_model=ScopeView, deprecated=True)
@inject_sync
def edit_scope(
    slug: str,
    request: ScopeEditRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.GRAPH_EDIT))],
    door: RequestDoor,
    registry: Registry,
    scopes: Scopes,
    project: CallProject,
    if_match: Annotated[int | None, Header()] = None,
) -> ScopeView:
    """Apply only the fields present; an edit that changes nothing writes nothing. 422 on a
    malformed slug naming the rejected value or on a `null` field, 404 on a well-formed but
    unknown slug, 409 on a stale `If-Match` naming the current revision. Deprecated: `PATCH
    /api/projects/{project}/scopes/{slug}`."""
    return _edit(slug, request, identity, door, registry, scopes, project, if_match)


@router.post("/scopes/{slug}/retire", response_model=ScopeView, status_code=status.HTTP_202_ACCEPTED, deprecated=True)
@inject_sync
def retire_scope(
    slug: str,
    request: ScopeLifecycleRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.GRAPH_EDIT))],
    door: RequestDoor,
    lifecycle: Lifecycle,
    scopes: Scopes,
    project: CallProject,
    if_match: Annotated[int | None, Header()] = None,
) -> ScopeView:
    """Retire a scope — a reversible brake; retiring a retired scope writes nothing. 404 on
    an unknown slug, 409 on a stale `If-Match`. Deprecated: `POST /api/projects/{project}/scopes/{slug}/retire`."""
    return _set_retired(True, slug, request, identity, door, lifecycle, scopes, project, if_match)


@router.post("/scopes/{slug}/enable", response_model=ScopeView, status_code=status.HTTP_202_ACCEPTED, deprecated=True)
@inject_sync
def enable_scope(
    slug: str,
    request: ScopeLifecycleRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.GRAPH_EDIT))],
    door: RequestDoor,
    lifecycle: Lifecycle,
    scopes: Scopes,
    project: CallProject,
    if_match: Annotated[int | None, Header()] = None,
) -> ScopeView:
    """Re-enable a retired scope; enabling an enabled one writes nothing. 404 on an unknown
    slug, 409 on a stale `If-Match`. Deprecated: `POST /api/projects/{project}/scopes/{slug}/enable`."""
    return _set_retired(False, slug, request, identity, door, lifecycle, scopes, project, if_match)


# --- under /api/projects/{project} ---


@project_router.post("/scopes", response_model=ScopeView, status_code=status.HTTP_201_CREATED)
@inject_sync
def create_project_scope(
    request: ScopeCreateRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.GRAPH_EDIT))],
    door: RequestDoor,
    registry: Registry,
    project: PathProject,
) -> ScopeView:
    """Mint a scope in the project, or no-op onto its existing one of the same slug; 422 on a malformed slug,
    naming the rejected value; 404 on an unknown project."""
    return _create(request, identity, door, registry, project)


@project_router.get("/scopes", response_model=list[ScopeView], dependencies=[_VIEW])
@inject_sync
def list_project_scopes(scopes: Scopes, projects: ProjectReads, project: PathProject) -> list[ScopeView]:
    """Every scope of the project, newest first, each marked retired or not; 404 on an unknown project."""
    return _listing(scopes, projects, project.project_id)


@project_router.get("/scopes/{slug}", response_model=ScopeView, dependencies=[_VIEW])
@inject_sync
def get_project_scope(slug: str, scopes: Scopes, project: PathProject) -> ScopeView:
    """One scope of the project; 404 on an unknown project or slug."""
    return _labelled(_scope_or_404(project, slug, scopes), project)


@project_router.get("/scopes/{slug}/routines", response_model=list[str], dependencies=[_VIEW])
@inject_sync
def list_project_scope_routines(
    slug: str, scopes: Scopes, routine_scopes: RoutineScopes, project: PathProject
) -> list[str]:
    """Every routine id linked to the project's scope `slug`; 404 on an unknown project or slug."""
    scope = _scope_or_404(project, slug, scopes)
    return routine_scopes.list_routines(scope.scope_id)


@project_router.patch("/scopes/{slug}", response_model=ScopeView)
@inject_sync
def edit_project_scope(
    slug: str,
    request: ScopeEditRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.GRAPH_EDIT))],
    door: RequestDoor,
    registry: Registry,
    scopes: Scopes,
    project: PathProject,
    if_match: Annotated[int | None, Header()] = None,
) -> ScopeView:
    """Apply only the fields present to the project's scope `slug`; an edit that changes nothing writes nothing.
    422 on a malformed slug or a `null` field, 404 on an unknown project or slug, 409 on a stale `If-Match`."""
    return _edit(slug, request, identity, door, registry, scopes, project, if_match)


@project_router.post("/scopes/{slug}/retire", response_model=ScopeView, status_code=status.HTTP_202_ACCEPTED)
@inject_sync
def retire_project_scope(
    slug: str,
    request: ScopeLifecycleRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.GRAPH_EDIT))],
    door: RequestDoor,
    lifecycle: Lifecycle,
    scopes: Scopes,
    project: PathProject,
    if_match: Annotated[int | None, Header()] = None,
) -> ScopeView:
    """Retire the project's scope `slug`; retiring a retired scope writes nothing. 404 on an unknown project or
    slug, 409 on a stale `If-Match`."""
    return _set_retired(True, slug, request, identity, door, lifecycle, scopes, project, if_match)


@project_router.post("/scopes/{slug}/enable", response_model=ScopeView, status_code=status.HTTP_202_ACCEPTED)
@inject_sync
def enable_project_scope(
    slug: str,
    request: ScopeLifecycleRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.GRAPH_EDIT))],
    door: RequestDoor,
    lifecycle: Lifecycle,
    scopes: Scopes,
    project: PathProject,
    if_match: Annotated[int | None, Header()] = None,
) -> ScopeView:
    """Re-enable the project's retired scope `slug`; enabling an enabled one writes nothing. 404 on an unknown
    project or slug, 409 on a stale `If-Match`."""
    return _set_retired(False, slug, request, identity, door, lifecycle, scopes, project, if_match)
