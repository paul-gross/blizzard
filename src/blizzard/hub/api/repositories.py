"""Repository routes — the configured-record verbs (create, list, show, patch, retire,
enable). Writes go only through
``ConfigAuthoring``; the actor and door come from the request's :class:`ChangeContext`.
The records are inert."""

from __future__ import annotations

from collections.abc import Mapping
from functools import partial
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response, status

from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api import problems, problems_admin
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.credential_choice import credential_choice
from blizzard.hub.api.deps import Services
from blizzard.hub.api.door import RequestDoor, change_context
from blizzard.hub.api.forge_choice import forge_choice
from blizzard.hub.api.forge_warnings import warned
from blizzard.hub.api.problem_exceptions import ProblemHTTPException
from blizzard.hub.api.repository_params import PathRepository
from blizzard.hub.api.user_names import user_names
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.auth.permissions import Permission
from blizzard.hub.composition import TenantServices
from blizzard.hub.domain.config.authoring import Authored
from blizzard.hub.domain.config.credential_naming import CREDENTIAL_FIELD, CredentialId
from blizzard.hub.domain.config.credentials import ConfiguredCredential
from blizzard.hub.domain.config.errors import ConfigFieldError, ConfigRevisionConflict
from blizzard.hub.domain.config.forge_resolution import FORGE_FIELD
from blizzard.hub.domain.config.forges import ConfiguredForge
from blizzard.hub.domain.config.repositories import (
    ConfiguredRepository,
    RepositoryCoordinateTaken,
    RepositoryDraft,
    RepositoryEdit,
    RepositorySlugTaken,
)
from blizzard.wire.repository import (
    RepositoriesListView,
    RepositoryCreateRequest,
    RepositoryPatchRequest,
    RepositorySummary,
)

router = APIRouter(
    prefix="/api", tags=["repositories"], dependencies=[Depends(reject_runner_principal), Depends(tenant_bound)]
)


def _summary(
    record: ConfiguredRepository,
    forge: ConfiguredForge,
    credential: ConfiguredCredential | None,
    names: Mapping[str, str],
) -> RepositorySummary:
    fields = record.fields
    return RepositorySummary(
        repository_id=record.repository_id,
        slug=record.slug,
        name=record.name,
        forge_id=forge.forge_id,
        forge_slug=forge.slug,
        forge_name=forge.name,
        owner=fields.owner,
        repo=fields.repo,
        base_branch=fields.base_branch,
        secret_name=fields.secret_name,
        landing_policy=fields.landing_policy,
        allow_no_ci=fields.allow_no_ci,
        park_timeout_hours=fields.park_timeout_hours,
        revision=record.revision,
        created_at=iso_utc(record.created_at),
        created_by=record.created_by,
        created_by_name=names.get(record.created_by),
        retired=record.retired,
        credential_id=None if credential is None else credential.credential_id,
        credential_slug=None if credential is None else credential.slug,
        credential_name=None if credential is None else credential.name,
    )


def _credentials(
    records: list[ConfiguredRepository], services: TenantServices
) -> dict[CredentialId, ConfiguredCredential]:
    return services.credential_records.get_many(
        list({r.fields.credential_id for r in records if r.fields.credential_id is not None})
    )


def _shown(record: ConfiguredRepository, services: TenantServices) -> RepositorySummary:
    """``record``'s summary, with the forge and credential it names."""
    forge = services.forge_records.get(record.fields.forge_id)
    assert forge is not None
    credential_id = record.fields.credential_id
    credential = None if credential_id is None else services.credential_records.get(credential_id)
    return _summary(record, forge, credential, user_names(services, [record.created_by]))


def _authored(
    authored: Authored[ConfiguredRepository], response: Response, services: TenantServices
) -> RepositorySummary:
    """The written record's summary, carrying a warning for each old field the write resolved."""
    shown = _shown(authored.record, services)
    forge = services.forge_records.get(authored.record.fields.forge_id)
    assert forge is not None
    shown.warnings = warned(response, services, authored.old_fields, record=f"repository {shown.slug}", forge=forge)
    return shown


def _unprocessable(exc: ConfigFieldError) -> ProblemHTTPException:
    return ProblemHTTPException(
        partial(problems_admin.config_field_invalid_problem, "body", exc.field, exc.message, exc.code)
    )


def _conflict(exc: Exception) -> ProblemHTTPException:
    if isinstance(exc, RepositorySlugTaken):
        return ProblemHTTPException(partial(problems_admin.repository_slug_taken_problem, str(exc)))
    if isinstance(exc, RepositoryCoordinateTaken):
        return ProblemHTTPException(partial(problems_admin.repository_coordinate_taken_problem, str(exc)))
    return ProblemHTTPException(partial(problems.revision_conflict_problem, str(exc)))


@router.get(
    "/repositories", response_model=RepositoriesListView, dependencies=[Depends(require(Permission.FLEET_VIEW))]
)
def list_repositories(services: Services, include_retired: bool = False) -> RepositoriesListView:
    """Every stored repository by slug, retired ones hidden unless `include_retired`."""
    records = services.repository_records.list_all(include_retired=include_retired)
    names = user_names(services, (r.created_by for r in records))
    forges = services.forge_records.get_many(list({r.fields.forge_id for r in records}))
    credentials = _credentials(records, services)
    return RepositoriesListView(
        repositories=[
            _summary(
                r,
                forges[r.fields.forge_id],
                None if r.fields.credential_id is None else credentials.get(r.fields.credential_id),
                names,
            )
            for r in records
        ]
    )


@router.post("/repositories", response_model=RepositorySummary, status_code=status.HTTP_201_CREATED)
def create_repository(
    request: RepositoryCreateRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))],
    door: RequestDoor,
    services: Services,
    response: Response,
) -> RepositorySummary:
    """Store a new repository at revision 1 on the forge `forge` names; its `name` is its `slug` when omitted. 422
    naming the field for a malformed `slug` or `name`, a blank field, or a missing or retired secret, with the code
    `forge_required` for no `forge`, `forge_unavailable` for an unknown or retired forge, and `field_moved_to_forge`
    for a `forge_api_url`, which the forge carries — naming that forge where one can be told; 409 for a slug some
    repository holds or a taken `(forge, owner, repo)`, naming the holder.

    The repository acts with the credential `credential` names, or the `github.token` credential reading the
    deprecated `secret_name` on the forge — created when there is none, and answered with a `Deprecation` header and
    a `warnings` entry. 422 coded `credential_unavailable` for an unknown or retired credential,
    `credential_forge_mismatch` for one of another forge, `credential_conflict` for a `secret_name` that is not the
    credential's secret, and `forge_capability_missing` for a landing policy the credential cannot honour."""
    draft = RepositoryDraft(
        owner=request.owner,
        repo=request.repo,
        base_branch=request.base_branch,
        secret_name=request.secret_name,
        landing_policy=request.landing_policy,
        allow_no_ci=request.allow_no_ci,
        park_timeout_hours=request.park_timeout_hours,
    )
    try:
        authored = services.config_authoring.create_repository_naming(
            request.slug,
            request.name,
            draft,
            forge_choice(request, services),
            change_context(identity, door),
            credential=credential_choice(request, services),
        )
    except ConfigFieldError as exc:
        raise _unprocessable(exc) from exc
    except (RepositorySlugTaken, RepositoryCoordinateTaken) as exc:
        raise _conflict(exc) from exc
    return _authored(authored, response, services)


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
    response: Response,
    if_match: Annotated[int | None, Header()] = None,
) -> RepositorySummary:
    """Apply only the fields present; an explicit `null` clears `park_timeout_hours` and is refused naming any other
    field. A patch that changes nothing writes nothing. A slug that changes keeps resolving to the repository until
    another claims it. 404 unknown; 409 for a stale `If-Match` (naming the current revision), a slug some repository
    holds, or a taken `(forge, owner, repo)`; 422 naming the field, coded as on create. `forge` re-points the
    repository at a forge, and `credential` at a credential, resolved and refused as on create; a deprecated
    `secret_name` other than the repository's resolves as on create. Moving the forge, or changing `secret_name`, of a
    repository acting with a credential needs a `credential` too: 422 `credential_forge_mismatch` or
    `credential_conflict` without one."""
    present = request.model_fields_set
    stated = {field: getattr(request, field) for field in present if field not in (FORGE_FIELD, CREDENTIAL_FIELD)}
    edit = RepositoryEdit(**stated)
    try:
        authored = services.config_authoring.edit_repository_naming(
            record,
            edit,
            forge_choice(request, services),
            change_context(identity, door),
            if_match=if_match,
            credential=credential_choice(request, services),
        )
    except ConfigFieldError as exc:
        raise _unprocessable(exc) from exc
    except (ConfigRevisionConflict, RepositoryCoordinateTaken, RepositorySlugTaken) as exc:
        raise _conflict(exc) from exc
    return _authored(authored, response, services)


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
