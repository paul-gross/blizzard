"""Human-plane forge record reads and authoring verbs."""

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
from blizzard.hub.api.forge_params import PathForge
from blizzard.hub.api.user_names import user_names
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.composition import TenantServices
from blizzard.hub.domain.config.errors import ConfigFieldError, ConfigRevisionConflict
from blizzard.hub.domain.config.forges import (
    ConfiguredForge,
    ForgeEdit,
    ForgeFields,
    ForgeInUse,
    ForgeOriginTaken,
    ForgeSlugTaken,
)
from blizzard.wire.forge import (
    ForgeCreateRequest,
    ForgeDetail,
    ForgePatchRequest,
    ForgeReferenceView,
    ForgesListView,
    ForgeSummary,
)

router = APIRouter(
    prefix="/api/forges", tags=["forges"], dependencies=[Depends(reject_runner_principal), Depends(tenant_bound)]
)


def _summary(record: ConfiguredForge, names: Mapping[str, str]) -> ForgeSummary:
    return ForgeSummary(
        forge_id=record.forge_id,
        slug=record.slug,
        name=record.name,
        kind=record.kind,
        api_base=record.fields.api_base,
        web_base=record.fields.web_base,
        web_origin=record.web_origin,
        revision=record.revision,
        created_at=iso_utc(record.created_at),
        created_by=record.created_by,
        created_by_name=names.get(record.created_by),
        retired=record.retired,
    )


def _detail(record: ConfiguredForge, services: TenantServices) -> ForgeDetail:
    repositories = services.repository_records.list_all(include_retired=True)
    sources = services.work_source_records.list_all(include_retired=True)
    return ForgeDetail(
        **_summary(record, user_names(services, [record.created_by])).model_dump(),
        repositories=[
            ForgeReferenceView(kind="repository", key=r.repository_id, label=r.slug)
            for r in repositories
            if r.fields.forge_id == record.forge_id
        ],
        work_sources=[
            ForgeReferenceView(kind="work_source", key=s.work_source_id, label=s.slug)
            for s in sources
            if s.fields.forge_id == record.forge_id
        ],
    )


def _unprocessable(exc: ConfigFieldError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=[{"loc": ["body", exc.field], "msg": exc.message, "type": exc.code}],
    )


def _conflict(exc: Exception) -> HTTPException:
    if isinstance(exc, ForgeInUse):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": exc.code,
                "message": str(exc),
                "referrers": [
                    ForgeReferenceView(kind=r.kind.value, key=r.key, label=r.label).model_dump() for r in exc.referrers
                ],
            },
        )
    detail: dict[str, object] = {"code": "revision_conflict", "message": str(exc)}
    if isinstance(exc, ConfigRevisionConflict):
        detail["current"] = exc.current
    elif isinstance(exc, ForgeOriginTaken):
        detail.update(code=exc.code, holder=exc.holder, api_base=exc.api_base)
    elif isinstance(exc, ForgeSlugTaken):
        detail.update(code=exc.code, slug=exc.slug)
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


@router.get("", response_model=ForgesListView, dependencies=[Depends(require(Permission.FLEET_VIEW))])
def list_forges(services: Services, include_retired: bool = False) -> ForgesListView:
    """List forges by slug, including retired ones when requested."""
    records = services.forge_records.list_all(include_retired=include_retired)
    names = user_names(services, (r.created_by for r in records))
    return ForgesListView(forges=[_summary(r, names) for r in records])


@router.post("", response_model=ForgeDetail, status_code=status.HTTP_201_CREATED)
def create_forge(
    request: ForgeCreateRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))],
    door: RequestDoor,
    services: Services,
) -> ForgeDetail:
    """Create a forge at revision one. Invalid fields answer 422; taken slug or API origin answers 409."""
    try:
        record = services.config_authoring.create_forge(
            request.slug,
            request.name,
            request.kind,
            ForgeFields(request.api_base, request.web_base),
            change_context(identity, door),
        )
    except ConfigFieldError as exc:
        raise _unprocessable(exc) from exc
    except (ForgeSlugTaken, ForgeOriginTaken) as exc:
        raise _conflict(exc) from exc
    return _detail(record, services)


@router.get("/{forge}", response_model=ForgeDetail, dependencies=[Depends(require(Permission.FLEET_VIEW))])
def get_forge(forge: PathForge, services: Services) -> ForgeDetail:
    """Show a forge by id, current slug or former slug, with linked records."""
    return _detail(forge, services)


@router.patch("/{forge}", response_model=ForgeDetail)
def patch_forge(
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))],
    forge: PathForge,
    request: ForgePatchRequest,
    door: RequestDoor,
    services: Services,
    if_match: Annotated[int | None, Header()] = None,
) -> ForgeDetail:
    """Edit stated fields, keeping the revision on a no-op. A stale If-Match answers 409."""
    edit = ForgeEdit(**{field: getattr(request, field) for field in request.model_fields_set})
    try:
        record = services.config_authoring.edit_forge(forge, edit, change_context(identity, door), if_match=if_match)
    except ConfigFieldError as exc:
        raise _unprocessable(exc) from exc
    except (ConfigRevisionConflict, ForgeSlugTaken, ForgeOriginTaken) as exc:
        raise _conflict(exc) from exc
    return _detail(record, services)


def _lifecycle(
    forge: ConfiguredForge,
    identity: ResolvedIdentity,
    door: RequestDoor,
    services: TenantServices,
    if_match: int | None,
    *,
    retired: bool,
) -> ForgeDetail:
    try:
        verb = services.config_authoring.retire_forge if retired else services.config_authoring.enable_forge
        record = verb(forge, change_context(identity, door), if_match=if_match)
    except (ConfigRevisionConflict, ForgeInUse) as exc:
        raise _conflict(exc) from exc
    return _detail(record, services)


@router.post("/{forge}/retire", response_model=ForgeDetail)
def retire_forge(
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))],
    forge: PathForge,
    door: RequestDoor,
    services: Services,
    if_match: Annotated[int | None, Header()] = None,
) -> ForgeDetail:
    """Retire a forge only when no enabled configured record names it."""
    return _lifecycle(forge, identity, door, services, if_match, retired=True)


@router.post("/{forge}/enable", response_model=ForgeDetail)
def enable_forge(
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))],
    forge: PathForge,
    door: RequestDoor,
    services: Services,
    if_match: Annotated[int | None, Header()] = None,
) -> ForgeDetail:
    """Enable a retired forge; an enabled forge remains unchanged."""
    return _lifecycle(forge, identity, door, services, if_match, retired=False)
