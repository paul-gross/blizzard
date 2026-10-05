"""Scope routes — create, list, read, edit, retire, and enable a scope.

The controller stays read-only over the store (``bzh:controller-read-only``), resolving a
slug into an object before delegating to the domain (``bzh:domain-takes-objects``). Every
write carries the request's :class:`ChangeContext` and an optional ``If-Match``
(``bzh:configured-record``). ``reject_runner_principal`` confines a runner's bearer token
to the fleet router."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status

from blizzard.auth_core import FLEET_VIEW, GRAPH_EDIT
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.api.door import RequestDoor, change_context
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.config.work_sources import ConfigFieldError, ConfigRevisionConflict
from blizzard.hub.domain.garden.scopes import Scope, ScopeEdit, ScopeSlug, ScopeSlugError
from blizzard.wire.scope import ScopeCreateRequest, ScopeEditRequest, ScopeLifecycleRequest, ScopeView

router = APIRouter(prefix="/api", tags=["scopes"], dependencies=[Depends(reject_runner_principal)])


def scope_view(scope: Scope) -> ScopeView:
    """The one `Scope` -> `ScopeView` projection — reused as-is by the runner-facing
    fleet route (`blizzard.hub.api.fleet`) rather than restated there."""
    return ScopeView(
        slug=scope.slug,
        description=scope.description,
        created_at=iso_utc(scope.created_at),
        retired=scope.retired,
        revision=scope.revision,
    )


def _unprocessable(exc: ConfigFieldError) -> HTTPException:
    """A refused field, located in the body — the shape FastAPI's own validation answers."""
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=[{"loc": ["body", exc.field], "msg": exc.message, "type": "value_error"}],
    )


def _revision_conflict(exc: ConfigRevisionConflict) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


def _scope_or_404(slug: str, services: HubServices) -> Scope:
    scope = services.scopes.get(slug)
    if scope is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown scope {slug}")
    return scope


@router.post("/scopes", response_model=ScopeView, status_code=status.HTTP_201_CREATED)
def create_scope(
    request: ScopeCreateRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(GRAPH_EDIT))],
    door: RequestDoor,
    services: Annotated[HubServices, Depends(get_services)],
) -> ScopeView:
    """Mint a scope, or no-op onto the existing one of the same slug; 422 on a
    malformed slug, naming the rejected value."""
    try:
        slug = ScopeSlug.parse(request.slug)
    except ScopeSlugError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    scope = services.scope_registry.ensure(slug, change_context(identity, door), description=request.description)
    return scope_view(scope)


@router.get("/scopes", response_model=list[ScopeView], dependencies=[Depends(require(FLEET_VIEW))])
def list_scopes(services: Annotated[HubServices, Depends(get_services)]) -> list[ScopeView]:
    """Every scope, newest first, each marked retired or not."""
    return [scope_view(s) for s in services.scopes.list_all()]


@router.get("/scopes/{slug}", response_model=ScopeView, dependencies=[Depends(require(FLEET_VIEW))])
def get_scope(slug: str, services: Annotated[HubServices, Depends(get_services)]) -> ScopeView:
    """One scope; 404 on an unknown slug."""
    return scope_view(_scope_or_404(slug, services))


@router.get(
    "/scopes/{slug}/routines",
    response_model=list[str],
    dependencies=[Depends(require(FLEET_VIEW))],
)
def list_scope_routines(slug: str, services: Annotated[HubServices, Depends(get_services)]) -> list[str]:
    """Every routine id linked to `slug` — the reverse direction of
    `GET /api/routines/{routine_id}/scopes`. 404 on an unknown slug."""
    scope = _scope_or_404(slug, services)
    return services.routine_scopes.list_routines(scope.slug)


@router.patch("/scopes/{slug}", response_model=ScopeView)
def edit_scope(
    slug: str,
    request: ScopeEditRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(GRAPH_EDIT))],
    door: RequestDoor,
    services: Annotated[HubServices, Depends(get_services)],
    if_match: Annotated[int | None, Header()] = None,
) -> ScopeView:
    """Apply only the fields present; an edit that changes nothing writes nothing. 422 on a
    malformed slug naming the rejected value or on a `null` field, 404 on a well-formed but
    unknown slug, 409 on a stale `If-Match` naming the current revision."""
    try:
        parsed = ScopeSlug.parse(slug)
    except ScopeSlugError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    scope = _scope_or_404(parsed.value, services)
    edit = ScopeEdit(**{name: getattr(request, name) for name in request.model_fields_set})
    try:
        edited = services.scope_registry.edit(scope, edit, change_context(identity, door), if_match=if_match)
    except ConfigFieldError as exc:
        raise _unprocessable(exc) from exc
    except ConfigRevisionConflict as exc:
        raise _revision_conflict(exc) from exc
    return scope_view(edited)


@router.post("/scopes/{slug}/retire", response_model=ScopeView, status_code=status.HTTP_202_ACCEPTED)
def retire_scope(
    slug: str,
    request: ScopeLifecycleRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(GRAPH_EDIT))],
    door: RequestDoor,
    services: Annotated[HubServices, Depends(get_services)],
    if_match: Annotated[int | None, Header()] = None,
) -> ScopeView:
    """Retire a scope — a reversible brake; retiring a retired scope writes nothing. 404 on
    an unknown slug, 409 on a stale `If-Match`."""
    scope = _scope_or_404(slug, services)
    try:
        retired = services.scope_lifecycle.retire(
            scope, change_context(identity, door), by=request.by, if_match=if_match
        )
    except ConfigRevisionConflict as exc:
        raise _revision_conflict(exc) from exc
    return scope_view(retired)


@router.post("/scopes/{slug}/enable", response_model=ScopeView, status_code=status.HTTP_202_ACCEPTED)
def enable_scope(
    slug: str,
    request: ScopeLifecycleRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(GRAPH_EDIT))],
    door: RequestDoor,
    services: Annotated[HubServices, Depends(get_services)],
    if_match: Annotated[int | None, Header()] = None,
) -> ScopeView:
    """Re-enable a retired scope; enabling an enabled one writes nothing. 404 on an unknown
    slug, 409 on a stale `If-Match`."""
    scope = _scope_or_404(slug, services)
    try:
        enabled = services.scope_lifecycle.enable(
            scope, change_context(identity, door), by=request.by, if_match=if_match
        )
    except ConfigRevisionConflict as exc:
        raise _revision_conflict(exc) from exc
    return scope_view(enabled)
