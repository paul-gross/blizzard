"""Secret routes — create, replace, list, read, retire, and enable a secret.

Write-only (``bzh:secret-write-only``): a value enters through a request and no route
returns it, a ciphertext, or a key id. The controller reaches only the metadata catalog
(``bzh:controller-read-only``); the actor recorded on every write is the authenticated
identity, never a request field."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from blizzard.auth_core import CONFIG_EDIT, FLEET_VIEW
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.api.door import RequestDoor, change_context
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.config.changes import RecordRef
from blizzard.hub.domain.config.secrets import (
    SecretAlreadyExists,
    SecretMetadata,
    SecretName,
    SecretNameError,
    SecretReferenced,
    SecretRetired,
    SecretRevisionConflict,
)
from blizzard.wire.secret import RecordRefView, SecretCreateRequest, SecretReplaceRequest, SecretView

SECRETS_PREFIX = "/api/secrets"

router = APIRouter(prefix=SECRETS_PREFIX, tags=["secrets"], dependencies=[Depends(reject_runner_principal)])


def secret_view(record: SecretMetadata, *, retired: bool, references: list[RecordRef] | None = None) -> SecretView:
    return SecretView(
        name=record.name,
        revision=record.revision,
        replaced_at=iso_utc(record.replaced_at),
        replaced_by=record.replaced_by,
        created_at=iso_utc(record.created_at),
        retired=retired,
        references=[RecordRefView(kind=r.kind.value, key=r.key) for r in references or []],
    )


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
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc


def _existing(services: HubServices, raw: str) -> SecretMetadata:
    record = services.secret_catalog.get(_parse_name(raw).value)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown secret {raw}")
    return record


@router.post("", response_model=SecretView, status_code=status.HTTP_201_CREATED)
def create_secret(
    request: SecretCreateRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(CONFIG_EDIT))],
    door: RequestDoor,
    services: Annotated[HubServices, Depends(get_services)],
) -> SecretView:
    """Store a new secret at revision 1; 409 when the name is taken, 422 on a malformed name."""
    name = _parse_name(request.name)
    try:
        record = services.config_authoring.create_secret(
            name, request.value.get_secret_value(), change_context(identity, door)
        )
    except SecretAlreadyExists as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return secret_view(record, retired=False)


@router.put("/{name}/value", response_model=SecretView)
def replace_secret(
    name: str,
    request: SecretReplaceRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(CONFIG_EDIT))],
    door: RequestDoor,
    services: Annotated[HubServices, Depends(get_services)],
    if_match: Annotated[int | None, Header()] = None,
) -> SecretView:
    """Replace a secret's value, advancing its revision. 409 naming the current revision
    when `If-Match` is stale or a concurrent replace won, and when the secret is retired;
    404 on an unknown name."""
    record = _existing(services, name)
    try:
        replaced = services.config_authoring.replace_secret(
            record, request.value.get_secret_value(), change_context(identity, door), if_match=if_match
        )
    except SecretRevisionConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except SecretRetired as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"{exc}; enable it first") from exc
    return secret_view(replaced, retired=False, references=services.secret_references.referrers_of([name])[name])


@router.get("", response_model=list[SecretView], dependencies=[Depends(require(FLEET_VIEW))])
def list_secrets(
    services: Annotated[HubServices, Depends(get_services)], include_retired: bool = False
) -> list[SecretView]:
    """Every secret's metadata, retired ones hidden unless `include_retired`."""
    retired = services.secret_catalog.retired_names()
    records = [r for r in services.secret_catalog.list_all() if include_retired or r.name not in retired]
    references = services.secret_references.referrers_of([r.name for r in records])
    return [secret_view(r, retired=r.name in retired, references=references[r.name]) for r in records]


@router.get("/{name}", response_model=SecretView, dependencies=[Depends(require(FLEET_VIEW))])
def get_secret(name: str, services: Annotated[HubServices, Depends(get_services)]) -> SecretView:
    """One secret's metadata; 404 on an unknown name."""
    record = _existing(services, name)
    return secret_view(
        record,
        retired=services.secret_catalog.is_retired(record.name),
        references=services.secret_references.referrers_of([record.name])[record.name],
    )


@router.post("/{name}/retire", response_model=SecretView)
def retire_secret(
    name: str,
    identity: Annotated[ResolvedIdentity, Depends(require(CONFIG_EDIT))],
    door: RequestDoor,
    services: Annotated[HubServices, Depends(get_services)],
) -> SecretView:
    """Retire a secret — a reversible brake; 404 on an unknown name, 409 naming the active
    records that still reference it."""
    record = _existing(services, name)
    try:
        services.config_authoring.retire_secret(record, change_context(identity, door))
    except SecretReferenced as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return secret_view(record, retired=True, references=services.secret_references.referrers_of([name])[name])


@router.post("/{name}/enable", response_model=SecretView)
def enable_secret(
    name: str,
    identity: Annotated[ResolvedIdentity, Depends(require(CONFIG_EDIT))],
    door: RequestDoor,
    services: Annotated[HubServices, Depends(get_services)],
) -> SecretView:
    """Re-enable a retired secret; idempotent, 404 on an unknown name."""
    record = _existing(services, name)
    services.config_authoring.enable_secret(record, change_context(identity, door))
    return secret_view(record, retired=False, references=services.secret_references.referrers_of([name])[name])
