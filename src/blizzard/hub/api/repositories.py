"""Repository routes — the configured-record verbs (create, list, show, patch, retire,
enable), human-plane throughout (``reject_runner_principal``). Writes go only through
``ConfigAuthoring``; the actor and door come from the request's :class:`ChangeContext`.
The records are inert: delivery still takes its target from the hub's forge settings."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status

from blizzard.auth_core import CONFIG_EDIT, FLEET_VIEW
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.api.door import RequestDoor, change_context
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.config.repositories import (
    ConfiguredRepository,
    RepositoryCoordinateTaken,
    RepositoryEdit,
    RepositoryFields,
    RepositoryNameTaken,
)
from blizzard.hub.domain.config.work_sources import ConfigFieldError, ConfigRevisionConflict
from blizzard.wire.repository import (
    RepositoriesListView,
    RepositoryDocument,
    RepositoryPatchRequest,
    RepositorySummary,
)

router = APIRouter(prefix="/api", tags=["repositories"], dependencies=[Depends(reject_runner_principal)])


def _summary(record: ConfiguredRepository) -> RepositorySummary:
    fields = record.fields
    return RepositorySummary(
        name=record.name,
        forge_api_url=fields.forge_api_url,
        owner=fields.owner,
        repo=fields.repo,
        base_branch=fields.base_branch,
        secret_name=fields.secret_name,
        revision=record.revision,
        created_at=iso_utc(record.created_at),
        created_by=record.created_by,
        retired=record.retired,
    )


def _unprocessable(exc: ConfigFieldError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=[{"loc": ["body", exc.field], "msg": exc.message, "type": "value_error"}],
    )


def _conflict(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


def _stored(name: str, services: HubServices) -> ConfiguredRepository:
    record = services.repository_records.get(name)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown repository {name!r}")
    return record


@router.get("/repositories", response_model=RepositoriesListView, dependencies=[Depends(require(FLEET_VIEW))])
def list_repositories(
    services: Annotated[HubServices, Depends(get_services)], include_retired: bool = False
) -> RepositoriesListView:
    """Every stored repository by name, retired ones hidden unless `include_retired`."""
    records = services.repository_records.list_all(include_retired=include_retired)
    return RepositoriesListView(repositories=[_summary(r) for r in records])


@router.post("/repositories", response_model=RepositorySummary, status_code=status.HTTP_201_CREATED)
def create_repository(
    request: RepositoryDocument,
    identity: Annotated[ResolvedIdentity, Depends(require(CONFIG_EDIT))],
    door: RequestDoor,
    services: Annotated[HubServices, Depends(get_services)],
) -> RepositorySummary:
    """Store a new repository at revision 1. 422 naming the field for a blank field, a
    non-http(s) `forge_api_url`, or a missing or retired secret; 409 for a taken name or a
    taken `(forge_api_url, owner, repo)`, naming the holder."""
    fields = RepositoryFields(
        forge_api_url=request.forge_api_url,
        owner=request.owner,
        repo=request.repo,
        base_branch=request.base_branch,
        secret_name=request.secret_name,
    )
    try:
        record = services.config_authoring.create_repository(request.name, fields, change_context(identity, door))
    except ConfigFieldError as exc:
        raise _unprocessable(exc) from exc
    except (RepositoryNameTaken, RepositoryCoordinateTaken) as exc:
        raise _conflict(exc) from exc
    return _summary(record)


@router.get("/repositories/{name}", response_model=RepositorySummary, dependencies=[Depends(require(FLEET_VIEW))])
def get_repository(name: str, services: Annotated[HubServices, Depends(get_services)]) -> RepositorySummary:
    """One repository, retired or not. 404 on an unknown name."""
    return _summary(_stored(name, services))


@router.patch("/repositories/{name}", response_model=RepositorySummary)
def patch_repository(
    name: str,
    request: RepositoryPatchRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(CONFIG_EDIT))],
    door: RequestDoor,
    services: Annotated[HubServices, Depends(get_services)],
    if_match: Annotated[int | None, Header()] = None,
) -> RepositorySummary:
    """Apply only the fields present; an explicit `null` is refused naming the field. A patch
    that changes nothing writes nothing. 404 unknown; 409 for a stale `If-Match` (naming the
    current revision) or a taken `(forge_api_url, owner, repo)`; 422 naming the field."""
    record = _stored(name, services)
    present = request.model_fields_set
    edit = RepositoryEdit(**{field: getattr(request, field) for field in present})
    try:
        edited = services.config_authoring.edit_repository(
            record, edit, change_context(identity, door), if_match=if_match
        )
    except ConfigFieldError as exc:
        raise _unprocessable(exc) from exc
    except (ConfigRevisionConflict, RepositoryCoordinateTaken) as exc:
        raise _conflict(exc) from exc
    return _summary(edited)


@router.post("/repositories/{name}/retire", response_model=RepositorySummary)
def retire_repository(
    name: str,
    identity: Annotated[ResolvedIdentity, Depends(require(CONFIG_EDIT))],
    door: RequestDoor,
    services: Annotated[HubServices, Depends(get_services)],
    if_match: Annotated[int | None, Header()] = None,
) -> RepositorySummary:
    """Retire a repository — a reversible brake that keeps its coordinate claim. Retiring a
    retired repository changes nothing. 404 unknown, 409 for a stale `If-Match`."""
    record = _stored(name, services)
    try:
        retired = services.config_authoring.retire_repository(record, change_context(identity, door), if_match=if_match)
    except ConfigRevisionConflict as exc:
        raise _conflict(exc) from exc
    return _summary(retired)


@router.post("/repositories/{name}/enable", response_model=RepositorySummary)
def enable_repository(
    name: str,
    identity: Annotated[ResolvedIdentity, Depends(require(CONFIG_EDIT))],
    door: RequestDoor,
    services: Annotated[HubServices, Depends(get_services)],
    if_match: Annotated[int | None, Header()] = None,
) -> RepositorySummary:
    """Re-enable a retired repository; enabling an active one changes nothing. 404 unknown,
    409 for a stale `If-Match`, 422 when its secret has since been retired."""
    record = _stored(name, services)
    try:
        enabled = services.config_authoring.enable_repository(record, change_context(identity, door), if_match=if_match)
    except ConfigFieldError as exc:
        raise _unprocessable(exc) from exc
    except ConfigRevisionConflict as exc:
        raise _conflict(exc) from exc
    return _summary(enabled)
