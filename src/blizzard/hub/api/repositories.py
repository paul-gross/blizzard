"""Repository routes — the configured-record verbs (create, list, show, patch, retire,
enable), human-plane throughout (``reject_runner_principal``). Writes go only through
``ConfigAuthoring``; the actor and door come from the request's :class:`ChangeContext`.
The records are inert."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status

from blizzard.auth_core import Permission
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.deps import Services
from blizzard.hub.api.door import RequestDoor, change_context
from blizzard.hub.api.repository_params import PathRepository
from blizzard.hub.api.user_names import user_names
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.composition import TenantServices
from blizzard.hub.domain.config.errors import ConfigFieldError, ConfigRevisionConflict
from blizzard.hub.domain.config.forge_resolution import ForgeChoice
from blizzard.hub.domain.config.forges import ConfiguredForge, ForgeOriginTaken, ForgeSlugTaken
from blizzard.hub.domain.config.repositories import (
    ConfiguredRepository,
    RepositoryCoordinateTaken,
    RepositoryDraft,
    RepositoryEdit,
    RepositorySlugTaken,
)
from blizzard.hub.domain.kernel.unset import UNSET
from blizzard.wire.repository import (
    RepositoriesListView,
    RepositoryDocument,
    RepositoryPatchRequest,
    RepositorySummary,
)

router = APIRouter(
    prefix="/api", tags=["repositories"], dependencies=[Depends(reject_runner_principal), Depends(tenant_bound)]
)


#: The old address field a repository names its forge by.
_ADDRESS = "forge_api_url"


def _summary(record: ConfiguredRepository, forge: ConfiguredForge, names: Mapping[str, str]) -> RepositorySummary:
    fields = record.fields
    return RepositorySummary(
        repository_id=record.repository_id,
        slug=record.slug,
        name=record.name,
        forge_api_url=forge.fields.api_base,
        owner=fields.owner,
        repo=fields.repo,
        base_branch=fields.base_branch,
        secret_name=fields.secret_name,
        revision=record.revision,
        created_at=iso_utc(record.created_at),
        created_by=record.created_by,
        created_by_name=names.get(record.created_by),
        retired=record.retired,
    )


def _shown(record: ConfiguredRepository, services: TenantServices) -> RepositorySummary:
    """``record``'s summary, its address read from its forge."""
    forge = services.forge_records.get(record.fields.forge_id)
    assert forge is not None
    return _summary(record, forge, user_names(services, [record.created_by]))


def _unprocessable(exc: ConfigFieldError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=[{"loc": ["body", exc.field], "msg": exc.message, "type": "value_error"}],
    )


def _conflict(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.get(
    "/repositories", response_model=RepositoriesListView, dependencies=[Depends(require(Permission.FLEET_VIEW))]
)
def list_repositories(services: Services, include_retired: bool = False) -> RepositoriesListView:
    """Every stored repository by slug, retired ones hidden unless `include_retired`."""
    records = services.repository_records.list_all(include_retired=include_retired)
    names = user_names(services, (r.created_by for r in records))
    forges = services.forge_records.get_many(list({r.fields.forge_id for r in records}))
    return RepositoriesListView(repositories=[_summary(r, forges[r.fields.forge_id], names) for r in records])


@router.post("/repositories", response_model=RepositorySummary, status_code=status.HTTP_201_CREATED)
def create_repository(
    request: RepositoryDocument,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))],
    door: RequestDoor,
    services: Services,
) -> RepositorySummary:
    """Store a new repository at revision 1; its `name` is its `slug` when omitted. 422 naming the field for a
    malformed `slug` or `name`, a blank field, a non-http(s) `forge_api_url`, or a missing or retired secret; 409 for
    a slug some repository holds or a taken `(forge_api_url, owner, repo)`, naming the holder."""
    draft = RepositoryDraft(
        owner=request.owner, repo=request.repo, base_branch=request.base_branch, secret_name=request.secret_name
    )
    try:
        record = services.config_authoring.create_repository_naming(
            request.slug,
            request.name,
            draft,
            ForgeChoice(api_base=request.forge_api_url),
            change_context(identity, door),
        ).record
    except ConfigFieldError as exc:
        raise _unprocessable(exc) from exc
    except (RepositorySlugTaken, RepositoryCoordinateTaken, ForgeSlugTaken, ForgeOriginTaken) as exc:
        raise _conflict(exc) from exc
    return _shown(record, services)


@router.get(
    "/repositories/{repository}",
    response_model=RepositorySummary,
    dependencies=[Depends(require(Permission.FLEET_VIEW))],
)
def get_repository(repository: PathRepository, services: Services) -> RepositorySummary:
    """One repository by id or slug — a slug it left included — retired or not. 404 on an unknown repository."""
    return _shown(repository, services)


@router.patch("/repositories/{repository}", response_model=RepositorySummary)
def patch_repository(
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))],
    record: PathRepository,
    request: RepositoryPatchRequest,
    door: RequestDoor,
    services: Services,
    if_match: Annotated[int | None, Header()] = None,
) -> RepositorySummary:
    """Apply only the fields present; an explicit `null` is refused naming the field. A patch
    that changes nothing writes nothing. A slug that changes keeps resolving to the repository until another claims
    it. 404 unknown; 409 for a stale `If-Match` (naming the current revision), a slug some repository holds, or a
    taken `(forge_api_url, owner, repo)`; 422 naming the field."""
    present = request.model_fields_set
    edit = RepositoryEdit(**{field: getattr(request, field) for field in present if field != _ADDRESS})
    choice = ForgeChoice(api_base=request.forge_api_url if _ADDRESS in present else UNSET)
    try:
        edited = services.config_authoring.edit_repository_naming(
            record, edit, choice, change_context(identity, door), if_match=if_match
        ).record
    except ConfigFieldError as exc:
        raise _unprocessable(exc) from exc
    except (
        ConfigRevisionConflict,
        RepositoryCoordinateTaken,
        RepositorySlugTaken,
        ForgeSlugTaken,
        ForgeOriginTaken,
    ) as exc:
        raise _conflict(exc) from exc
    return _shown(edited, services)


@router.post("/repositories/{repository}/retire", response_model=RepositorySummary)
def retire_repository(
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))],
    record: PathRepository,
    door: RequestDoor,
    services: Services,
    if_match: Annotated[int | None, Header()] = None,
) -> RepositorySummary:
    """Retire a repository — a reversible brake that keeps its coordinate claim. Retiring a
    retired repository changes nothing. 404 unknown, 409 for a stale `If-Match`."""
    try:
        retired = services.config_authoring.retire_repository(record, change_context(identity, door), if_match=if_match)
    except ConfigRevisionConflict as exc:
        raise _conflict(exc) from exc
    return _shown(retired, services)


@router.post("/repositories/{repository}/enable", response_model=RepositorySummary)
def enable_repository(
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))],
    record: PathRepository,
    door: RequestDoor,
    services: Services,
    if_match: Annotated[int | None, Header()] = None,
) -> RepositorySummary:
    """Re-enable a retired repository; enabling an active one changes nothing. 404 unknown,
    409 for a stale `If-Match`, 422 when its secret has since been retired."""
    try:
        enabled = services.config_authoring.enable_repository(record, change_context(identity, door), if_match=if_match)
    except ConfigFieldError as exc:
        raise _unprocessable(exc) from exc
    except ConfigRevisionConflict as exc:
        raise _conflict(exc) from exc
    return _shown(enabled, services)
