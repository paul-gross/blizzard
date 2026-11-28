"""Human-plane forge record reads and authoring verbs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject_sync
from fastapi import APIRouter, Depends, Header, status

from blizzard.auth_core import Permission
from blizzard.foundation.instants import iso_utc
from blizzard.hub.api import problems_admin
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import RequestTenant, require, tenant_bound
from blizzard.hub.api.door import RequestDoor, change_context
from blizzard.hub.api.forge_params import PathForge
from blizzard.hub.api.problem_exceptions import ProblemHTTPException
from blizzard.hub.api.user_names import user_names
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.auth.users import IReadUserRepository
from blizzard.hub.domain.config.authoring import ConfigAuthoring
from blizzard.hub.domain.config.credential_bindings import secret_of_authority
from blizzard.hub.domain.config.errors import ConfigFieldError, ConfigRevisionConflict
from blizzard.hub.domain.config.forges import (
    ConfiguredForge,
    ForgeEdit,
    ForgeFields,
    ForgeInUse,
    ForgeOriginTaken,
    ForgeSlugTaken,
    IReadForgeRepository,
)
from blizzard.hub.domain.config.landing_policies import POLICY_CAPABILITIES, missing_capabilities
from blizzard.hub.domain.config.repositories import IReadRepositoryRecordRepository
from blizzard.hub.domain.config.work_sources import IReadWorkSourceRepository
from blizzard.hub.forge.budget import ForgeBudgets
from blizzard.hub.forge.registry import ForgeRegistry
from blizzard.wire.forge import (
    ForgeBudgetView,
    ForgeCreateRequest,
    ForgeDetail,
    ForgePatchRequest,
    ForgeReferenceView,
    ForgesListView,
    ForgeSummary,
    PolicyHonour,
)
from blizzard.wire.record_referrer import RecordReferrer

router = APIRouter(
    prefix="/api/forges", tags=["forges"], dependencies=[Depends(reject_runner_principal), Depends(tenant_bound)]
)
#: The tenant's forge records, its one config writer and the user reads, each resolved through the request's scope.
ForgeRecords = FromDishka[IReadForgeRepository]
Authoring = FromDishka[ConfigAuthoring]
Users = FromDishka[IReadUserRepository]


@dataclass(frozen=True)
class _DetailReads:
    """What a forge's detail reads beside the record: the records naming it, its roles, its budgets and the users."""

    repositories: IReadRepositoryRecordRepository
    source_records: IReadWorkSourceRepository
    forges: ForgeRegistry
    budgets: ForgeBudgets
    users: IReadUserRepository


@inject_sync
def _detail_reads(
    _tenant: RequestTenant,
    repositories: FromDishka[IReadRepositoryRecordRepository],
    source_records: FromDishka[IReadWorkSourceRepository],
    forges: FromDishka[ForgeRegistry],
    budgets: FromDishka[ForgeBudgets],
    users: Users,
) -> _DetailReads:
    return _DetailReads(repositories, source_records, forges, budgets, users)


DetailReads = Annotated[_DetailReads, Depends(_detail_reads)]


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


def _detail(record: ConfiguredForge, reads: _DetailReads) -> ForgeDetail:
    repositories = reads.repositories.list_all(include_retired=True)
    sources = reads.source_records.list_all(include_retired=True)
    offered = reads.forges.offered(record)
    return ForgeDetail(
        **_summary(record, user_names(reads.users, [record.created_by])).model_dump(),
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
        offered=sorted(offered),
        landing_policies=[
            PolicyHonour(policy=policy, honoured=not missing, missing=sorted(missing))
            for policy in POLICY_CAPABILITIES
            for missing in [missing_capabilities(policy, offered)]
        ],
        budgets=[
            ForgeBudgetView(
                secret_name=secret_of_authority(key.authority),
                authority=key.authority,
                bucket=reading.bucket,
                limit=reading.limit,
                remaining=reading.remaining,
                reset=iso_utc(reading.reset),
            )
            for key, reading in reads.budgets.forge_readings(record.forge_id)
        ],
    )


def _unprocessable(exc: ConfigFieldError) -> ProblemHTTPException:
    return ProblemHTTPException(
        partial(problems_admin.config_field_invalid_problem, "body", exc.field, exc.message, exc.code)
    )


def _conflict(exc: Exception) -> ProblemHTTPException:
    if isinstance(exc, ForgeInUse):
        referrers = [RecordReferrer(kind=r.kind.value, key=r.key, label=r.label) for r in exc.referrers]
        return ProblemHTTPException(partial(problems_admin.forge_in_use_problem, str(exc), referrers))
    if isinstance(exc, ForgeOriginTaken):
        return ProblemHTTPException(
            partial(problems_admin.forge_origin_taken_problem, str(exc), exc.holder, exc.api_base)
        )
    if isinstance(exc, ForgeSlugTaken):
        return ProblemHTTPException(partial(problems_admin.forge_slug_taken_problem, str(exc), exc.slug))
    assert isinstance(exc, ConfigRevisionConflict)
    return ProblemHTTPException(partial(problems_admin.forge_revision_conflict_problem, str(exc), exc.current))


@router.get("", response_model=ForgesListView, dependencies=[Depends(require(Permission.FLEET_VIEW))])
@inject_sync
def list_forges(forge_records: ForgeRecords, users: Users, include_retired: bool = False) -> ForgesListView:
    """List forges by slug, including retired ones when requested."""
    records = forge_records.list_all(include_retired=include_retired)
    names = user_names(users, (r.created_by for r in records))
    return ForgesListView(forges=[_summary(r, names) for r in records])


@router.post("", response_model=ForgeDetail, status_code=status.HTTP_201_CREATED)
@inject_sync
def create_forge(
    request: ForgeCreateRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))],
    door: RequestDoor,
    authoring: Authoring,
    reads: DetailReads,
) -> ForgeDetail:
    """Create a forge at revision one. Invalid fields answer 422; taken slug or API origin answers 409."""
    try:
        record = authoring.create_forge(
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
    return _detail(record, reads)


@router.get("/{forge}", response_model=ForgeDetail, dependencies=[Depends(require(Permission.FLEET_VIEW))])
def get_forge(forge: PathForge, reads: DetailReads) -> ForgeDetail:
    """Show a forge by id, current slug or former slug, with linked records."""
    return _detail(forge, reads)


@router.patch("/{forge}", response_model=ForgeDetail)
@inject_sync
def patch_forge(
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))],
    forge: PathForge,
    request: ForgePatchRequest,
    door: RequestDoor,
    authoring: Authoring,
    reads: DetailReads,
    if_match: Annotated[int | None, Header()] = None,
) -> ForgeDetail:
    """Edit stated fields, keeping the revision on a no-op. A stale If-Match answers 409."""
    edit = ForgeEdit(**{field: getattr(request, field) for field in request.model_fields_set})
    try:
        record = authoring.edit_forge(forge, edit, change_context(identity, door), if_match=if_match)
    except ConfigFieldError as exc:
        raise _unprocessable(exc) from exc
    except (ConfigRevisionConflict, ForgeSlugTaken, ForgeOriginTaken) as exc:
        raise _conflict(exc) from exc
    return _detail(record, reads)


def _lifecycle(
    forge: ConfiguredForge,
    identity: ResolvedIdentity,
    door: RequestDoor,
    authoring: ConfigAuthoring,
    reads: _DetailReads,
    if_match: int | None,
    *,
    retired: bool,
) -> ForgeDetail:
    try:
        verb = authoring.retire_forge if retired else authoring.enable_forge
        record = verb(forge, change_context(identity, door), if_match=if_match)
    except (ConfigRevisionConflict, ForgeInUse) as exc:
        raise _conflict(exc) from exc
    return _detail(record, reads)


@router.post("/{forge}/retire", response_model=ForgeDetail)
@inject_sync
def retire_forge(
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))],
    forge: PathForge,
    door: RequestDoor,
    authoring: Authoring,
    reads: DetailReads,
    if_match: Annotated[int | None, Header()] = None,
) -> ForgeDetail:
    """Retire a forge only when no enabled configured record names it."""
    return _lifecycle(forge, identity, door, authoring, reads, if_match, retired=True)


@router.post("/{forge}/enable", response_model=ForgeDetail)
@inject_sync
def enable_forge(
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))],
    forge: PathForge,
    door: RequestDoor,
    authoring: Authoring,
    reads: DetailReads,
    if_match: Annotated[int | None, Header()] = None,
) -> ForgeDetail:
    """Enable a retired forge; an enabled forge remains unchanged."""
    return _lifecycle(forge, identity, door, authoring, reads, if_match, retired=False)
