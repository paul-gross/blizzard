"""Scope routes — create, list, read, edit, retire, and enable a scope.

A scope's slug names it within its project, so every by-slug route and every create sits under
``/api/projects/{project}``. The un-nested ``/api/scopes`` routes stay: the listing reads the whole
tenant or the project ``?project=`` names, and each by-slug route and the create remain as deprecated
aliases acting in the call's project — the tenant's only one, else ``422 project_required``.

The controller resolves a slug into an object before delegating to the domain
(``bzh:controller-read-only``, ``bzh:domain-takes-objects``). Every write carries the
request's :class:`ChangeContext` and an optional ``If-Match`` (``bzh:configured-record``).
``reject_runner_principal`` confines a runner's bearer token to the fleet router."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status

from blizzard.auth_core import Permission
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.deps import Services
from blizzard.hub.api.door import RequestDoor, change_context
from blizzard.hub.api.project_label_fields import ProjectLabelFields
from blizzard.hub.api.project_labels import ProjectLabels
from blizzard.hub.api.project_params import CallProject, PathProject, QueryProject
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.composition import TenantServices
from blizzard.hub.domain.config.errors import ConfigFieldError, ConfigRevisionConflict
from blizzard.hub.domain.garden.scopes import Scope, ScopeEdit, ScopeSlug, ScopeSlugError
from blizzard.hub.domain.projects.model import Project
from blizzard.wire.scope import ScopeCreateRequest, ScopeEditRequest, ScopeLifecycleRequest, ScopeView

_GUARDS = [Depends(reject_runner_principal), Depends(tenant_bound)]
router = APIRouter(prefix="/api", tags=["scopes"], dependencies=_GUARDS)
project_router = APIRouter(prefix="/api/projects/{project}", tags=["scopes"], dependencies=_GUARDS)
_VIEW = Depends(require(Permission.FLEET_VIEW))


def scope_view(scope: Scope, labels: ProjectLabelFields | None = None) -> ScopeView:
    """The one `Scope` -> `ScopeView` projection, labelled with its project when ``labels`` is given — reused
    as-is by the runner-facing fleet route (`blizzard.hub.api.fleet`) rather than restated there."""
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
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=[{"loc": ["body", exc.field], "msg": exc.message, "type": "value_error"}],
    )


def _revision_conflict(exc: ConfigRevisionConflict) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


def _scope_or_404(project: Project, slug: str, services: TenantServices) -> Scope:
    scope = services.scopes.get(project.project_id, slug)
    if scope is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown scope {slug}")
    return scope


def _create(
    request: ScopeCreateRequest,
    identity: ResolvedIdentity,
    door: RequestDoor,
    services: TenantServices,
    project: Project,
) -> ScopeView:
    try:
        slug = ScopeSlug.parse(request.slug)
    except ScopeSlugError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    scope = services.scope_registry.ensure(
        project, slug, change_context(identity, door), description=request.description
    )
    return _labelled(scope, project)


def _edit(
    slug: str,
    request: ScopeEditRequest,
    identity: ResolvedIdentity,
    door: RequestDoor,
    services: TenantServices,
    project: Project,
    if_match: int | None,
) -> ScopeView:
    try:
        parsed = ScopeSlug.parse(slug)
    except ScopeSlugError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    scope = _scope_or_404(project, parsed.value, services)
    edit = ScopeEdit(**{name: getattr(request, name) for name in request.model_fields_set})
    try:
        edited = services.scope_registry.edit(scope, edit, change_context(identity, door), if_match=if_match)
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
    services: TenantServices,
    project: Project,
    if_match: int | None,
) -> ScopeView:
    scope = _scope_or_404(project, slug, services)
    verb = services.scope_lifecycle.retire if retire else services.scope_lifecycle.enable
    try:
        changed = verb(scope, change_context(identity, door), by=request.by, if_match=if_match)
    except ConfigRevisionConflict as exc:
        raise _revision_conflict(exc) from exc
    return _labelled(changed, project)


def _listing(services: TenantServices, project_id: str | None) -> list[ScopeView]:
    scopes = services.scopes.list_all(project_id)
    labels = ProjectLabels.read(services, (s.project_id for s in scopes))
    return [scope_view(s, labels.fields(s.project_id)) for s in scopes]


# --- the un-nested routes: the tenant-wide listing, and the deprecated by-slug aliases ---


@router.post("/scopes", response_model=ScopeView, status_code=status.HTTP_201_CREATED, deprecated=True)
def create_scope(
    request: ScopeCreateRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.GRAPH_EDIT))],
    door: RequestDoor,
    services: Services,
    project: CallProject,
) -> ScopeView:
    """Mint a scope in the call's project, or no-op onto the project's existing one of the same slug; 422 on a
    malformed slug, naming the rejected value. Deprecated: `POST /api/projects/{project}/scopes`."""
    return _create(request, identity, door, services, project)


@router.get("/scopes", response_model=list[ScopeView], dependencies=[_VIEW])
def list_scopes(services: Services, project: QueryProject) -> list[ScopeView]:
    """Every scope, newest first, each marked retired or not — of the project `?project=` names, else of every
    project."""
    return _listing(services, project)


@router.get("/scopes/{slug}", response_model=ScopeView, dependencies=[_VIEW], deprecated=True)
def get_scope(slug: str, services: Services, project: CallProject) -> ScopeView:
    """One scope of the call's project; 404 on an unknown slug. Deprecated: `GET
    /api/projects/{project}/scopes/{slug}`."""
    return _labelled(_scope_or_404(project, slug, services), project)


@router.get("/scopes/{slug}/routines", response_model=list[str], dependencies=[_VIEW], deprecated=True)
def list_scope_routines(slug: str, services: Services, project: CallProject) -> list[str]:
    """Every routine id linked to `slug` in the call's project — the reverse direction of
    `GET /api/routines/{routine_id}/scopes`. 404 on an unknown slug. Deprecated: `GET
    /api/projects/{project}/scopes/{slug}/routines`."""
    scope = _scope_or_404(project, slug, services)
    return services.routine_scopes.list_routines(scope.scope_id)


@router.patch("/scopes/{slug}", response_model=ScopeView, deprecated=True)
def edit_scope(
    slug: str,
    request: ScopeEditRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.GRAPH_EDIT))],
    door: RequestDoor,
    services: Services,
    project: CallProject,
    if_match: Annotated[int | None, Header()] = None,
) -> ScopeView:
    """Apply only the fields present; an edit that changes nothing writes nothing. 422 on a
    malformed slug naming the rejected value or on a `null` field, 404 on a well-formed but
    unknown slug, 409 on a stale `If-Match` naming the current revision. Deprecated: `PATCH
    /api/projects/{project}/scopes/{slug}`."""
    return _edit(slug, request, identity, door, services, project, if_match)


@router.post("/scopes/{slug}/retire", response_model=ScopeView, status_code=status.HTTP_202_ACCEPTED, deprecated=True)
def retire_scope(
    slug: str,
    request: ScopeLifecycleRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.GRAPH_EDIT))],
    door: RequestDoor,
    services: Services,
    project: CallProject,
    if_match: Annotated[int | None, Header()] = None,
) -> ScopeView:
    """Retire a scope — a reversible brake; retiring a retired scope writes nothing. 404 on
    an unknown slug, 409 on a stale `If-Match`. Deprecated: `POST /api/projects/{project}/scopes/{slug}/retire`."""
    return _set_retired(True, slug, request, identity, door, services, project, if_match)


@router.post("/scopes/{slug}/enable", response_model=ScopeView, status_code=status.HTTP_202_ACCEPTED, deprecated=True)
def enable_scope(
    slug: str,
    request: ScopeLifecycleRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.GRAPH_EDIT))],
    door: RequestDoor,
    services: Services,
    project: CallProject,
    if_match: Annotated[int | None, Header()] = None,
) -> ScopeView:
    """Re-enable a retired scope; enabling an enabled one writes nothing. 404 on an unknown
    slug, 409 on a stale `If-Match`. Deprecated: `POST /api/projects/{project}/scopes/{slug}/enable`."""
    return _set_retired(False, slug, request, identity, door, services, project, if_match)


# --- under /api/projects/{project} ---


@project_router.post("/scopes", response_model=ScopeView, status_code=status.HTTP_201_CREATED)
def create_project_scope(
    request: ScopeCreateRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.GRAPH_EDIT))],
    door: RequestDoor,
    services: Services,
    project: PathProject,
) -> ScopeView:
    """Mint a scope in the project, or no-op onto its existing one of the same slug; 422 on a malformed slug,
    naming the rejected value; 404 on an unknown project."""
    return _create(request, identity, door, services, project)


@project_router.get("/scopes", response_model=list[ScopeView], dependencies=[_VIEW])
def list_project_scopes(services: Services, project: PathProject) -> list[ScopeView]:
    """Every scope of the project, newest first, each marked retired or not; 404 on an unknown project."""
    return _listing(services, project.project_id)


@project_router.get("/scopes/{slug}", response_model=ScopeView, dependencies=[_VIEW])
def get_project_scope(slug: str, services: Services, project: PathProject) -> ScopeView:
    """One scope of the project; 404 on an unknown project or slug."""
    return _labelled(_scope_or_404(project, slug, services), project)


@project_router.get("/scopes/{slug}/routines", response_model=list[str], dependencies=[_VIEW])
def list_project_scope_routines(slug: str, services: Services, project: PathProject) -> list[str]:
    """Every routine id linked to the project's scope `slug`; 404 on an unknown project or slug."""
    scope = _scope_or_404(project, slug, services)
    return services.routine_scopes.list_routines(scope.scope_id)


@project_router.patch("/scopes/{slug}", response_model=ScopeView)
def edit_project_scope(
    slug: str,
    request: ScopeEditRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.GRAPH_EDIT))],
    door: RequestDoor,
    services: Services,
    project: PathProject,
    if_match: Annotated[int | None, Header()] = None,
) -> ScopeView:
    """Apply only the fields present to the project's scope `slug`; an edit that changes nothing writes nothing.
    422 on a malformed slug or a `null` field, 404 on an unknown project or slug, 409 on a stale `If-Match`."""
    return _edit(slug, request, identity, door, services, project, if_match)


@project_router.post("/scopes/{slug}/retire", response_model=ScopeView, status_code=status.HTTP_202_ACCEPTED)
def retire_project_scope(
    slug: str,
    request: ScopeLifecycleRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.GRAPH_EDIT))],
    door: RequestDoor,
    services: Services,
    project: PathProject,
    if_match: Annotated[int | None, Header()] = None,
) -> ScopeView:
    """Retire the project's scope `slug`; retiring a retired scope writes nothing. 404 on an unknown project or
    slug, 409 on a stale `If-Match`."""
    return _set_retired(True, slug, request, identity, door, services, project, if_match)


@project_router.post("/scopes/{slug}/enable", response_model=ScopeView, status_code=status.HTTP_202_ACCEPTED)
def enable_project_scope(
    slug: str,
    request: ScopeLifecycleRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.GRAPH_EDIT))],
    door: RequestDoor,
    services: Services,
    project: PathProject,
    if_match: Annotated[int | None, Header()] = None,
) -> ScopeView:
    """Re-enable the project's retired scope `slug`; enabling an enabled one writes nothing. 404 on an unknown
    project or slug, 409 on a stale `If-Match`."""
    return _set_retired(False, slug, request, identity, door, services, project, if_match)
