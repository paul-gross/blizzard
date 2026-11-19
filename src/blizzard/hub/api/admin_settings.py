"""Hub-wide settings, accessible only to a hub administrator."""

from __future__ import annotations

from functools import partial
from typing import Annotated, Any, NoReturn, Protocol, cast

from fastapi import APIRouter, Depends, Header, Query, Request

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
from blizzard.hub.auth.permissions import Permission
from blizzard.hub.composition import HubSettingsGraph
from blizzard.hub.settings.footing import SettingNotLive, SettingUnknown, require_settable, settable_declarations
from blizzard.hub.settings.values import thaw
from blizzard.wire.config import ConfigApplyOutcome, ConfigApplyResponse, FieldChangeView
from blizzard.wire.hub_settings import (
    HubSettingRow,
    HubSettingsChange,
    HubSettingsChanges,
    HubSettingsView,
    HubSettingsWrite,
)
from blizzard.wire.problem import ProblemDetails

router = APIRouter(prefix="/api/admin/settings", tags=["admin"], dependencies=[Depends(reject_runner_principal)])
Admin = Annotated[ResolvedIdentity, Depends(require_hub(Permission.SETTINGS_ADMIN))]


def graph(request: Request) -> HubSettingsGraph:
    settings = getattr(request.app.state, "hub_settings", None)
    if settings is None:
        raise ProblemHTTPException(problems.hub_store_unavailable_problem)
    return cast(HubSettingsGraph, settings)


Settings = Annotated[HubSettingsGraph, Depends(graph)]


class _ProblemBuilder(Protocol):
    def __call__(self, message: str, errors: dict[str, str], *, instance: str) -> ProblemDetails[Any]: ...


def _refuse(builder: _ProblemBuilder, message: str, errors: dict[str, str] | None = None) -> NoReturn:
    raise ProblemHTTPException(partial(builder, message, errors or {}))


def _check_keys(settings: HubSettingsGraph, keys: set[str]) -> None:
    try:
        require_settable(settings.footing.key_registry, keys)
    except SettingUnknown as exc:
        _refuse(setting_unknown_problem, str(exc))
    except SettingNotLive as exc:
        _refuse(setting_not_live_problem, str(exc))


def _view(settings: HubSettingsGraph) -> HubSettingsView:
    snapshot, provenance = settings.store.view()
    rows: list[HubSettingRow] = []
    for key in settable_declarations(settings.footing.key_registry):
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
def get_settings(identity: Admin, settings: Settings) -> HubSettingsView:
    return _view(settings)


@router.patch("", response_model=HubSettingsView)
def patch_settings(
    body: HubSettingsWrite,
    identity: Admin,
    door: RequestDoor,
    settings: Settings,
    if_match: Annotated[int | None, Header()] = None,
) -> HubSettingsView:
    _check_keys(settings, set(body.sets) | set(body.unset))
    if any(value is None for value in body.sets.values()):
        _refuse(settings_invalid_problem, "null is not a setting value; use the unset list")
    try:
        change = Change("edit", sets=body.sets, unsets=frozenset(body.unset))
        # The shared vocabulary names the page door "page"; the HTTP header names it "board".
        actual_door: Door = "page" if door.value == "board" else "cli" if door.value == "cli" else "api"
        settings.authoring.apply(change, if_match=if_match, door=actual_door, actor=identity.user_id)
    except ValueError as exc:
        _refuse(settings_invalid_problem, str(exc))
    except SettingsRevisionConflict as exc:
        raise ProblemHTTPException(partial(problems.revision_conflict_problem, str(exc))) from exc
    except SettingsInvalid as exc:
        _invalid(exc)
    return _view(settings)


@router.get("/changes", response_model=HubSettingsChanges)
def changes(
    identity: Admin,
    settings: Settings,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    before: Annotated[int | None, Query(ge=1)] = None,
) -> HubSettingsChanges:
    rows, next_before = settings.store.changes(limit, before)
    registry = settings.footing.key_registry
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


def _document(settings: HubSettingsGraph, document: dict[str, object]) -> dict[str, object]:
    registry = settings.footing.key_registry
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
    _check_keys(settings, set(result))
    return result


@router.post("/apply", response_model=ConfigApplyResponse)
def apply_document(
    document: dict[str, object], identity: Admin, settings: Settings, dry_run: bool = False
) -> ConfigApplyResponse:
    entries = _document(settings, document)
    if any(value is None for value in entries.values()):
        _refuse(settings_invalid_problem, "null is not a setting value; use settings unset")
    change = Change("edit", sets=entries)
    try:
        apply_id, diff = settings.authoring.apply_document(change, actor=identity.user_id, dry_run=dry_run)
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
                diff=[_field_view(settings.footing.key_registry, field)] if field else [],
            )
        )
    return ConfigApplyResponse(dry_run=dry_run, apply_id=apply_id, outcomes=outcomes)


@router.get("/export", response_model=dict[str, object])
def export_document(identity: Admin, settings: Settings) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, entry in settings.reader.snapshot().entries.items():
        declaration = settings.footing.key_registry.key(key)
        if declaration is not None and declaration.lifetime == Lifetime.PASS and not entry.at_default:
            parts = key.split(".")
            table = document
            for part in parts[:-1]:
                table = cast(dict[str, object], table.setdefault(part, {}))
            table[parts[-1]] = thaw(entry.value)
    return document
