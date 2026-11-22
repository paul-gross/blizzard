"""Secret routes — create, replace, list, read, retire, and enable a secret.

Write-only (``bzh:secret-write-only``): a value enters through a request and no route
returns it, a ciphertext, or a key id. The controller reaches only the metadata catalog
(``bzh:controller-read-only``); the actor recorded on every write is the authenticated
identity, never a request field."""

from __future__ import annotations

from collections.abc import Mapping
from functools import partial
from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject_sync
from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from blizzard.auth_core import Permission
from blizzard.foundation.secrets.value import SecretValueBlank
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api import problems, problems_admin
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.door import RequestDoor, change_context
from blizzard.hub.api.problem_exceptions import ProblemHTTPException
from blizzard.hub.api.user_names import user_names
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.auth.users import IReadUserRepository
from blizzard.hub.domain.config.authoring import ConfigAuthoring
from blizzard.hub.domain.config.changes import ISecretReferences, RecordRef
from blizzard.hub.domain.config.secrets import (
    ISecretCatalog,
    SecretAlreadyExists,
    SecretMetadata,
    SecretName,
    SecretNameError,
    SecretReferenced,
    SecretRetired,
    SecretRevisionConflict,
    listed,
    retirable,
)
from blizzard.wire.record_referrer import RecordReferrer
from blizzard.wire.secret import RecordRefView, SecretCreateRequest, SecretReplaceRequest, SecretView

SECRETS_PREFIX = "/api/secrets"

router = APIRouter(
    prefix=SECRETS_PREFIX, tags=["secrets"], dependencies=[Depends(reject_runner_principal), Depends(tenant_bound)]
)
#: The tenant's secret metadata, the records naming each secret, its one config writer and the user reads, each resolved
#: through the request's scope.
Catalog = FromDishka[ISecretCatalog]
References = FromDishka[ISecretReferences]
Authoring = FromDishka[ConfigAuthoring]
Users = FromDishka[IReadUserRepository]


def secret_view(
    record: SecretMetadata,
    names: Mapping[str, str],
    *,
    retired: bool,
    references: list[RecordRef] | None = None,
) -> SecretView:
    referrers = references or []
    return SecretView(
        name=record.name,
        revision=record.revision,
        replaced_at=iso_utc(record.replaced_at),
        replaced_by=record.replaced_by,
        replaced_by_name=names.get(record.replaced_by),
        created_at=iso_utc(record.created_at),
        retired=retired,
        retirable=retirable(referrers, retired=retired),
        references=[_ref_view(r) for r in referrers],
    )


def _ref_view(ref: RecordRef) -> RecordRefView:
    return RecordRefView(kind=ref.kind.value, key=ref.key, label=ref.label)


def sanitized_validation_response(request: Request, exc: RequestValidationError) -> JSONResponse | None:
    """A 422 for a secret route that names each offending field and never echoes the
    request input — FastAPI's default body would reflect a submitted value. ``None`` for
    any other route, so its 422 stays the framework default."""
    if not request.url.path.startswith(SECRETS_PREFIX):
        return None
    detail = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"detail": detail})


def _parse_name(raw: str) -> SecretName:
    try:
        return SecretName.parse(raw)
    except SecretNameError as exc:
        raise ProblemHTTPException(partial(problems_admin.secret_name_invalid_problem, str(exc))) from exc


def _existing(catalog: ISecretCatalog, raw: str) -> SecretMetadata:
    record = catalog.get(_parse_name(raw).value)
    if record is None:
        raise ProblemHTTPException(partial(problems.secret_not_found_problem, raw))
    return record


@router.post("", response_model=SecretView, status_code=status.HTTP_201_CREATED)
@inject_sync
def create_secret(
    request: SecretCreateRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))],
    door: RequestDoor,
    authoring: Authoring,
    users: Users,
) -> SecretView:
    """Store a new secret at revision 1; 409 when the name is taken, 422 on a malformed name
    or a blank value."""
    name = _parse_name(request.name)
    try:
        record = authoring.create_secret(name, request.value.get_secret_value(), change_context(identity, door))
    except SecretValueBlank as exc:
        raise ProblemHTTPException(partial(problems_admin.secret_value_blank_problem, str(exc))) from exc
    except SecretAlreadyExists as exc:
        raise ProblemHTTPException(partial(problems.secret_already_exists_problem, str(exc))) from exc
    return secret_view(record, user_names(users, [record.replaced_by]), retired=False)


@router.put("/{name}/value", response_model=SecretView)
@inject_sync
def replace_secret(
    name: str,
    request: SecretReplaceRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))],
    door: RequestDoor,
    catalog: Catalog,
    referrers: References,
    authoring: Authoring,
    users: Users,
    if_match: Annotated[int | None, Header()] = None,
) -> SecretView:
    """Replace a secret's value, advancing its revision. 409 naming the current revision
    when `If-Match` is stale or a concurrent replace won, and when the secret is retired;
    422 on a blank value; 404 on an unknown name."""
    record = _existing(catalog, name)
    try:
        replaced = authoring.replace_secret(
            record, request.value.get_secret_value(), change_context(identity, door), if_match=if_match
        )
    except SecretRevisionConflict as exc:
        raise ProblemHTTPException(partial(problems.revision_conflict_problem, str(exc))) from exc
    except SecretRetired as exc:
        raise ProblemHTTPException(partial(problems_admin.secret_retired_problem, f"{exc}; enable it first")) from exc
    except SecretValueBlank as exc:
        raise ProblemHTTPException(partial(problems_admin.secret_value_blank_problem, str(exc))) from exc
    return secret_view(
        replaced,
        user_names(users, [replaced.replaced_by]),
        retired=False,
        references=referrers.referrers_of([name])[name],
    )


@router.get("", response_model=list[SecretView], dependencies=[Depends(require(Permission.FLEET_VIEW))])
@inject_sync
def list_secrets(
    catalog: Catalog, referrers: References, users: Users, include_retired: bool = False
) -> list[SecretView]:
    """Every secret's metadata, retired ones hidden unless `include_retired`."""
    retired = catalog.retired_names()
    records = listed(catalog.list_all(), retired, include_retired=include_retired)
    references = referrers.referrers_of([r.name for r in records])
    names = user_names(users, (r.replaced_by for r in records))
    return [secret_view(r, names, retired=r.name in retired, references=references[r.name]) for r in records]


@router.get("/{name}", response_model=SecretView, dependencies=[Depends(require(Permission.FLEET_VIEW))])
@inject_sync
def get_secret(name: str, catalog: Catalog, referrers: References, users: Users) -> SecretView:
    """One secret's metadata; 404 on an unknown name."""
    record = _existing(catalog, name)
    return secret_view(
        record,
        user_names(users, [record.replaced_by]),
        retired=catalog.is_retired(record.name),
        references=referrers.referrers_of([record.name])[record.name],
    )


@router.post("/{name}/retire", response_model=SecretView)
@inject_sync
def retire_secret(
    name: str,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))],
    door: RequestDoor,
    catalog: Catalog,
    referrers: References,
    authoring: Authoring,
    users: Users,
) -> SecretView:
    """Retire a secret — a reversible brake; 404 on an unknown name, 409 naming the active
    records that still reference it."""
    record = _existing(catalog, name)
    try:
        authoring.retire_secret(record, change_context(identity, door))
    except SecretReferenced as exc:
        held = [RecordReferrer(kind=r.kind.value, key=r.key, label=r.label) for r in exc.referrers]
        raise ProblemHTTPException(partial(problems_admin.secret_in_use_problem, str(exc), held)) from exc
    return secret_view(
        record,
        user_names(users, [record.replaced_by]),
        retired=True,
        references=referrers.referrers_of([name])[name],
    )


@router.post("/{name}/enable", response_model=SecretView)
@inject_sync
def enable_secret(
    name: str,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))],
    door: RequestDoor,
    catalog: Catalog,
    referrers: References,
    authoring: Authoring,
    users: Users,
) -> SecretView:
    """Re-enable a retired secret; idempotent, 404 on an unknown name."""
    record = _existing(catalog, name)
    authoring.enable_secret(record, change_context(identity, door))
    return secret_view(
        record,
        user_names(users, [record.replaced_by]),
        retired=False,
        references=referrers.referrers_of([name])[name],
    )
