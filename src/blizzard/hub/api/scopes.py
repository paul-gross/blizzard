"""Scope routes — create, list, read, edit, retire, and enable a scope (issue #389).

The controller stays read-only over the store (``bzh:controller-read-only``), resolving a
slug into an object before delegating to the domain (``bzh:domain-takes-objects``).
``reject_runner_principal`` confines a runner's bearer token to the fleet router."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from blizzard.auth_core import FLEET_VIEW, GRAPH_EDIT
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.scopes import Scope, ScopeSlug, ScopeSlugError
from blizzard.wire.scope import ScopeCreateRequest, ScopeEditRequest, ScopeLifecycleRequest, ScopeView

router = APIRouter(prefix="/api", tags=["scopes"], dependencies=[Depends(reject_runner_principal)])


def _scope_view(scope: Scope, *, retired: bool) -> ScopeView:
    return ScopeView(
        slug=scope.slug, description=scope.description, created_at=iso_utc(scope.created_at), retired=retired
    )


@router.post(
    "/scopes",
    response_model=ScopeView,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(GRAPH_EDIT))],
)
def create_scope(request: ScopeCreateRequest, services: Annotated[HubServices, Depends(get_services)]) -> ScopeView:
    """Mint a scope, or no-op onto the existing one of the same slug (D4); 422 on a
    malformed slug, naming the rejected value."""
    try:
        slug = ScopeSlug.parse(request.slug)
    except ScopeSlugError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    scope = services.scope_registry.ensure(slug, description=request.description)
    return _scope_view(scope, retired=services.scopes.is_retired(scope.slug))


@router.get("/scopes", response_model=list[ScopeView], dependencies=[Depends(require(FLEET_VIEW))])
def list_scopes(services: Annotated[HubServices, Depends(get_services)]) -> list[ScopeView]:
    """Every scope, newest first, each marked retired or not."""
    return [_scope_view(s, retired=services.scopes.is_retired(s.slug)) for s in services.scopes.list_all()]


@router.get("/scopes/{slug}", response_model=ScopeView, dependencies=[Depends(require(FLEET_VIEW))])
def get_scope(slug: str, services: Annotated[HubServices, Depends(get_services)]) -> ScopeView:
    """One scope; 404 on an unknown slug."""
    scope = services.scopes.get(slug)
    if scope is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown scope {slug}")
    return _scope_view(scope, retired=services.scopes.is_retired(slug))


@router.patch("/scopes/{slug}", response_model=ScopeView, dependencies=[Depends(require(GRAPH_EDIT))])
def edit_scope(
    slug: str, request: ScopeEditRequest, services: Annotated[HubServices, Depends(get_services)]
) -> ScopeView:
    """Change a scope's description in place; 422 on a malformed slug naming the
    rejected value, 404 on a well-formed but unknown one."""
    try:
        parsed = ScopeSlug.parse(slug)
    except ScopeSlugError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    scope = services.scopes.get(parsed.value)
    if scope is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown scope {slug}")
    edited = services.scope_registry.edit(scope, description=request.description)
    return _scope_view(edited, retired=services.scopes.is_retired(slug))


@router.post(
    "/scopes/{slug}/retire",
    response_model=ScopeView,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(GRAPH_EDIT))],
)
def retire_scope(
    slug: str, request: ScopeLifecycleRequest, services: Annotated[HubServices, Depends(get_services)]
) -> ScopeView:
    """Retire a scope — a reversible brake (D3); 404 on an unknown slug."""
    scope = services.scopes.get(slug)
    if scope is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown scope {slug}")
    services.scope_lifecycle.retire(scope, by=request.by)
    return _scope_view(scope, retired=True)


@router.post(
    "/scopes/{slug}/enable",
    response_model=ScopeView,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(GRAPH_EDIT))],
)
def enable_scope(
    slug: str, request: ScopeLifecycleRequest, services: Annotated[HubServices, Depends(get_services)]
) -> ScopeView:
    """Re-enable a retired scope (D3); idempotent, 404 on an unknown slug."""
    scope = services.scopes.get(slug)
    if scope is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown scope {slug}")
    services.scope_lifecycle.enable(scope, by=request.by)
    return _scope_view(scope, retired=False)
