"""Human access to one runner's store-backed settings record."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from blizzard.foundation.config_keys import Lifetime
from blizzard.foundation.instants import iso_utc
from blizzard.foundation.settings_record.refusals import SettingsInvalid, SettingsRevisionConflict
from blizzard.foundation.settings_record.vocabulary import Entry
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.runner.api.federation import require_superuser
from blizzard.runner.api.problems import RunnerProblem, problem, reject_worker_credential
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.auth.session import RunnerSession
from blizzard.runner.settings.policy import SettingRefusal
from blizzard.runner.settings.ports import SettingsChange, SettingsSnapshot
from blizzard.wire.base import WireModel
from blizzard.wire.vocabulary import OPEN

router = APIRouter(prefix="/api/settings", tags=["runner settings"], dependencies=[Depends(reject_worker_credential)])


class SettingRow(WireModel):
    key: str
    value: object
    source: Annotated[Literal["entry", "default"] | Unrecognized, OPEN]
    lifetime: Annotated[Lifetime | Unrecognized, OPEN]
    revision: int | None = None
    written_at: str | None = None
    written_by: str | None = None


class FailureView(WireModel):
    revision: int
    error: str
    since: str


class InForce(WireModel):
    revision: int | None = None
    since: str | None = None
    failed: FailureView | None = None


class SettingsView(WireModel):
    revision: int
    in_force: InForce
    settings: list[SettingRow]
    settings_error: str | None = None


class EntryView(WireModel):
    value: object
    at_default: bool


class FieldChangeView(WireModel):
    key: str
    old: EntryView | None
    new: EntryView


class ChangeView(WireModel):
    id: int
    recorded_at: str
    revision: int
    op: str
    door: str
    actor: str
    apply_id: str | None
    diff: list[FieldChangeView]


class ChangesPage(WireModel):
    changes: list[ChangeView]
    next_before: int | None


class SettingsWrite(BaseModel):
    set: dict[str, object] = Field(default_factory=dict)
    unset: list[str] = Field(default_factory=list)


class SettingError(WireModel):
    key: str
    message: str


class SettingsProblem(RunnerProblem):
    key: str | None = None
    current: int | None = None
    errors: list[SettingError] | None = None
    #: Which record a host settings document's refused entry names — ``host``, ``runner``, ``workspace`` or
    #: ``subscription`` — as :class:`~blizzard.runner.api.host_settings.HostChangeView` names it.
    record_kind: str | None = None
    #: Which record of that kind; ``None`` for the host's own entries.
    record_key: str | None = None


SETTINGS_PROBLEM = {"schema": {"$ref": "#/components/schemas/SettingsProblem"}}
VALIDATION_ERROR = {"schema": {"$ref": "#/components/schemas/HTTPValidationError"}}
#: A settings write's refusals, as every record's ``PATCH`` declares them.
WRITE_RESPONSES: dict[int | str, dict[str, object]] = {
    403: {"description": "Superuser required"},
    409: {
        "description": "Settings revision conflict",
        "content": {"application/problem+json": SETTINGS_PROBLEM},
    },
    422: {
        "description": "Invalid settings edit or request",
        "content": {"application/problem+json": SETTINGS_PROBLEM, "application/json": VALIDATION_ERROR},
    },
}


def settings_problem(status: int, code: str, message: str, **members: object) -> JSONResponse:
    return problem(status, code, message, model=SettingsProblem, **members)


def _view(wiring: RunnerWiring) -> SettingsView:
    service = wiring.settings_service()
    stepper = wiring.stepper()
    snapshot: SettingsSnapshot
    provenance: Mapping[str, SettingsChange]

    (snapshot, provenance), in_force_revision, in_force_since, failure = stepper.settings_status(
        service.snapshot_with_provenance
    )
    in_force = InForce(
        revision=in_force_revision,
        since=in_force_since,
        failed=FailureView(revision=failure[0], error=failure[1], since=failure[2]) if failure else None,
    )
    try:
        resolved = service.resolve_for_read(snapshot)
    except Exception as exc:
        return SettingsView(
            revision=snapshot.revision, in_force=in_force, settings=[], settings_error=str(exc) or type(exc).__name__
        )
    defaults = resolved.settings_values()
    rows: list[SettingRow] = []
    for key, selection in service.effective(snapshot).items():
        change = provenance.get(selection.provenance_key)
        if selection.value_from == "identity":
            value = selection.identity_value
        elif selection.value_from == "entry":
            assert selection.entry is not None
            value = selection.entry.value
        else:
            value = defaults[key]
        rows.append(
            SettingRow(
                key=key,
                value=value,
                source=selection.source,
                lifetime=selection.lifetime,
                revision=change.revision if change else None,
                written_at=iso_utc(change.recorded_at) if change else None,
                written_by=change.actor if change else None,
            )
        )
    return SettingsView(
        revision=snapshot.revision,
        in_force=in_force,
        settings=rows,
    )


@router.get("", response_model=SettingsView)
def get_settings(request: Request) -> SettingsView:
    return _view(RunnerWiring.of(request))


@router.patch("", response_model=SettingsView, responses=WRITE_RESPONSES)
def patch_settings(
    request: Request,
    body: SettingsWrite,
    session: Annotated[RunnerSession, Depends(require_superuser)],
    if_match: Annotated[str | None, Header()] = None,
) -> SettingsView | JSONResponse:
    wiring = RunnerWiring.of(request)
    wiring.require_bound()
    try:
        revision = int(if_match) if if_match is not None else None
    except ValueError:
        revision = None
    try:
        wiring.settings_authoring().edit(
            body.set, frozenset(body.unset), if_match=revision, door="api", actor=session.username
        )
    except SettingRefusal as exc:
        return settings_problem(422, exc.code, str(exc), key=exc.key)
    except SettingsRevisionConflict as exc:
        return settings_problem(409, "revision_conflict", str(exc), current=exc.current)
    except SettingsInvalid as exc:
        return settings_problem(
            422,
            "settings_invalid",
            str(exc),
            errors=[{"key": key, "message": message} for key, message in exc.errors.items()],
        )
    return _view(wiring)


def entry_view(value: Entry | None) -> EntryView | None:
    return EntryView(value=value.value, at_default=value.at_default) if value is not None else None


@router.get("/changes", response_model=ChangesPage)
def settings_changes(
    request: Request, limit: Annotated[int, Query(ge=1, le=100)] = 50, before: Annotated[int | None, Query(ge=1)] = None
) -> ChangesPage:
    reader = RunnerWiring.of(request).settings_service()
    changes = reader.changes(limit=limit + 1, before=before)
    page = changes[:limit]
    return ChangesPage(
        changes=[
            ChangeView(
                id=c.id,
                recorded_at=iso_utc(c.recorded_at),
                revision=c.revision,
                op=c.op,
                door=c.door,
                actor=c.actor,
                apply_id=c.apply_id,
                diff=[
                    FieldChangeView(
                        key=d.key, old=entry_view(d.old), new=EntryView(value=d.new.value, at_default=d.new.at_default)
                    )
                    for d in c.diff
                ],
            )
            for c in page
        ],
        next_before=page[-1].id if len(changes) > limit else None,
    )
