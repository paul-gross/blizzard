"""Hub-wide settings, accessible only to a hub administrator."""

from __future__ import annotations

from functools import partial
from typing import Annotated, Any, NoReturn, Protocol, cast

from dishka.integrations.fastapi import FromDishka, inject_sync
from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from blizzard.auth_core import Permission
from blizzard.foundation.config_keys import TABLE, ConfigRegistry, Lifetime
from blizzard.foundation.settings_record.refusals import SettingsInvalid, SettingsRevisionConflict
from blizzard.foundation.settings_record.vocabulary import Change, Door, Entry, FieldChange
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api import problems
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require_hub
from blizzard.hub.api.door import RequestDoor
from blizzard.hub.api.problem_exceptions import ProblemHTTPException
from blizzard.hub.api.problems_admin import setting_not_live_problem, setting_unknown_problem, settings_invalid_problem
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.config import ConfigError
from blizzard.hub.documents import ConfigDecodeError, codec_for_media_type
from blizzard.hub.settings.footing import (
    HubSettingsFooting,
    SettingNotLive,
    SettingUnknown,
    require_settable,
    settable_declarations,
    validate_document_values,
)
from blizzard.hub.settings.footing import (
    export_document as export_settable_document,
)
from blizzard.hub.settings.model import IReadHubSettingsAdmin
from blizzard.hub.settings.reader import HubSettingsReader
from blizzard.hub.settings.service import HubSettingsAuthoring
from blizzard.hub.settings.values import thaw
from blizzard.wire.config import ConfigApplyOutcome, ConfigApplyResponse, FieldChangeView
from blizzard.wire.hub_settings import (
    SETTINGS_EXPORT_ADAPTER,
    HubSettingRow,
    HubSettingsChange,
    HubSettingsChanges,
    HubSettingsView,
    HubSettingsWrite,
    SettingsExportDocument,
)
from blizzard.wire.problem import ProblemDetails

router = APIRouter(prefix="/api/admin/settings", tags=["admin"], dependencies=[Depends(reject_runner_principal)])
Admin = Annotated[ResolvedIdentity, Depends(require_hub(Permission.SETTINGS_ADMIN))]


Store = FromDishka[IReadHubSettingsAdmin]
Reader = FromDishka[HubSettingsReader]
Authoring = FromDishka[HubSettingsAuthoring]
Footing = FromDishka[HubSettingsFooting]


class _ProblemBuilder(Protocol):
    def __call__(self, message: str, errors: dict[str, str], *, instance: str) -> ProblemDetails[Any]: ...


def _refuse(builder: _ProblemBuilder, message: str, errors: dict[str, str] | None = None) -> NoReturn:
    raise ProblemHTTPException(partial(builder, message, errors or {}))


def _check_keys(footing: HubSettingsFooting, keys: set[str]) -> None:
    try:
        require_settable(footing.key_registry, keys)
    except SettingUnknown as exc:
        _refuse(setting_unknown_problem, str(exc))
    except SettingNotLive as exc:
        _refuse(setting_not_live_problem, str(exc))


def _view(store: IReadHubSettingsAdmin, footing: HubSettingsFooting) -> HubSettingsView:
    snapshot, provenance = store.view()
    rows: list[HubSettingRow] = []
    for key in settable_declarations(footing.key_registry):
        entry = snapshot.entries.get(key.path)
        source = "entry" if entry is not None and not entry.at_default else "default"
        metadata = provenance.get(key.path)
        rows.append(
            HubSettingRow(
                key=key.path,
                value=thaw(entry.value if source == "entry" and entry else key.default),
                source="entry" if source == "entry" else "default",
                lifetime=Lifetime.PASS,
                revision=metadata[0] if metadata else None,
                written_at=iso_utc(metadata[1]) if metadata else None,
                written_by=metadata[2] if metadata else None,
            )
        )
    return HubSettingsView(revision=snapshot.revision, settings=rows)


def _invalid(exc: SettingsInvalid) -> NoReturn:
    _refuse(settings_invalid_problem, str(exc), exc.errors)


def _field_view(registry: ConfigRegistry, field: FieldChange) -> FieldChangeView:
    """A moved field with absent and at-default sides resolved to the value in force; a key no longer declared has no
    default to resolve to."""
    declaration = registry.key(field.key)
    default = declaration.default if declaration is not None else None
    return FieldChangeView(field=field.key, old=_effective(field.old, default), new=_effective(field.new, default))


def _effective(entry: Entry | None, default: object) -> object:
    return thaw(default if entry is None or entry.at_default else entry.value)


@router.get("", response_model=HubSettingsView)
@inject_sync
def get_settings(identity: Admin, store: Store, footing: Footing) -> HubSettingsView:
    return _view(store, footing)


@router.patch("", response_model=HubSettingsView)
@inject_sync
def patch_settings(
    body: HubSettingsWrite,
    identity: Admin,
    door: RequestDoor,
    store: Store,
    authoring: Authoring,
    footing: Footing,
    if_match: Annotated[int | None, Header()] = None,
) -> HubSettingsView:
    _check_keys(footing, set(body.sets) | set(body.unset))
    if any(value is None for value in body.sets.values()):
        _refuse(settings_invalid_problem, "null is not a setting value; use the unset list")
    try:
        change = Change("edit", sets=body.sets, unsets=frozenset(body.unset))
        # The shared vocabulary names the page door "page"; the HTTP header names it "board".
        actual_door: Door = "page" if door.value == "board" else "cli" if door.value == "cli" else "api"
        authoring.apply(change, if_match=if_match, door=actual_door, actor=identity.user_id)
    except ValueError as exc:
        _refuse(settings_invalid_problem, str(exc))
    except SettingsRevisionConflict as exc:
        raise ProblemHTTPException(partial(problems.revision_conflict_problem, str(exc))) from exc
    except SettingsInvalid as exc:
        _invalid(exc)
    return _view(store, footing)


@router.get("/changes", response_model=HubSettingsChanges)
@inject_sync
def changes(
    identity: Admin,
    store: Store,
    footing: Footing,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    before: Annotated[int | None, Query(ge=1)] = None,
) -> HubSettingsChanges:
    rows, next_before = store.changes(limit, before)
    registry = footing.key_registry
    return HubSettingsChanges(
        changes=[
            HubSettingsChange(
                id=row.revision,
                revision=row.revision,
                recorded_at=iso_utc(row.recorded_at),
                actor=row.actor,
                door=row.door,
                op=row.op,
                apply_id=row.apply_id,
                diff=[_field_view(registry, field) for field in row.diff],
            )
            for row in rows
        ],
        next_before=next_before,
    )


def _document(footing: HubSettingsFooting, document: dict[str, object]) -> dict[str, object]:
    try:
        validate_document_values(document)
    except ConfigError as exc:
        _refuse(settings_invalid_problem, str(exc))
    registry = footing.key_registry
    result: dict[str, object] = {}

    def walk(table: dict[str, object], prefix: str = "") -> None:
        for name, value in table.items():
            path = f"{prefix}.{name}" if prefix else name
            key = registry.key(path)
            if key is not None and key.type == TABLE and isinstance(value, dict):
                walk(cast(dict[str, object], value), path)
            else:
                result[path] = value

    walk(document)
    _check_keys(footing, set(result))
    return result


def _apply_document(
    document: SettingsExportDocument,
    identity: ResolvedIdentity,
    authoring: HubSettingsAuthoring,
    footing: HubSettingsFooting,
    dry_run: bool,
) -> ConfigApplyResponse:
    entries = _document(footing, document)
    if any(value is None for value in entries.values()):
        _refuse(settings_invalid_problem, "null is not a setting value; use settings unset")
    change = Change("edit", sets=entries)
    try:
        apply_id, diff = authoring.apply_document(change, actor=identity.user_id, dry_run=dry_run)
    except SettingsInvalid as exc:
        _invalid(exc)
    moved = {field.key: field for field in diff}
    outcomes: list[ConfigApplyOutcome] = []
    for key in entries:
        field = moved.get(key)
        outcomes.append(
            ConfigApplyOutcome(
                kind="hub",
                key=key,
                op="edit" if field else "unchanged",
                diff=[_field_view(footing.key_registry, field)] if field else [],
            )
        )
    return ConfigApplyResponse(dry_run=dry_run, apply_id=apply_id, outcomes=outcomes)


@inject_sync
def _apply_services(authoring: Authoring, footing: Footing) -> tuple[HubSettingsAuthoring, HubSettingsFooting]:
    return authoring, footing


_APPLY_BODY = {
    "required": True,
    "content": {
        media_type: {"schema": {"type": "object", "additionalProperties": True}}
        for media_type in ("application/json", "application/yaml", "application/toml")
    },
}


@router.post("/apply", response_model=ConfigApplyResponse, openapi_extra={"requestBody": _APPLY_BODY})
async def apply_document(
    request: Request,
    identity: Admin,
    services: Annotated[tuple[HubSettingsAuthoring, HubSettingsFooting], Depends(_apply_services)],
    dry_run: bool = False,
) -> ConfigApplyResponse:
    content_type = request.headers.get("content-type")
    codec = codec_for_media_type(content_type or "")
    if codec is None:
        raise ProblemHTTPException(
            partial(problems.config_media_type_unsupported_problem, f"unsupported content type {content_type!r}")
        )
    try:
        document = SETTINGS_EXPORT_ADAPTER.validate_python(codec.decode(await request.body()))
    except (ConfigDecodeError, ValidationError) as exc:
        _refuse(settings_invalid_problem, str(exc))
    return await run_in_threadpool(_apply_document, document, identity, *services, dry_run)


@router.get("/export", response_model=SettingsExportDocument)
@inject_sync
def export_document(identity: Admin, reader: Reader, footing: Footing) -> SettingsExportDocument:
    return export_settable_document(footing.key_registry, reader.snapshot().entries)
