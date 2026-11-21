"""Human access to the host's own store-backed settings record."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import Field

from blizzard.foundation.config_apply_op import ConfigApplyOp
from blizzard.foundation.settings_record.refusals import SettingsInvalid, SettingsRevisionConflict
from blizzard.foundation.settings_record.vocabulary import Entry
from blizzard.foundation.store.utc import iso_utc
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.runner.api.federation import require_superuser
from blizzard.runner.api.problems import reject_worker_credential
from blizzard.runner.api.settings import (
    SETTINGS_PROBLEM,
    WRITE_RESPONSES,
    ChangeView,
    EntryView,
    FailureView,
    FieldChangeView,
    InForce,
    SettingRow,
    SettingsView,
    SettingsWrite,
    entry_view,
    settings_problem,
)
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.auth.session import RunnerSession
from blizzard.runner.config import DEFAULT_TICK_SECONDS
from blizzard.runner.host_config import HostConfig
from blizzard.runner.settings.apply import ApplyInvalid, ApplyRefusal, DocumentOutcome
from blizzard.runner.settings.policy import SettingRefusal
from blizzard.runner.settings.ports import HostSettingsChange, SettingsSnapshot
from blizzard.wire.base import WireModel
from blizzard.wire.vocabulary import OPEN

router = APIRouter(
    prefix="/api/host/settings", tags=["host settings"], dependencies=[Depends(reject_worker_credential)]
)


class HostChangeView(ChangeView):
    """A host change row: the runner's, naming which of the host's records it moved."""

    #: Which kind of host record the row moved — ``host`` for the host's own entries; open, as later records add kinds.
    record_kind: str
    #: Which record of that kind; ``None`` for the host's own entries.
    record_key: str | None


class HostChangesPage(WireModel):
    changes: list[HostChangeView]
    next_before: int | None


def _values(config: HostConfig) -> dict[str, object]:
    """Every host key's value in the effective configuration, in the record's wire vocabulary."""
    tracing = config.tracing
    return {
        "tick_seconds": config.tick_seconds if config.tick_seconds is not None else DEFAULT_TICK_SECONDS,
        "transcripts.ship": config.transcripts.ship,
        "transcripts.record_max_bytes": config.transcripts.record_max_bytes,
        "transcripts.chunk_max_bytes": config.transcripts.chunk_max_bytes,
        "worker_stdout.retention_days": config.worker_stdout.retention_days,
        "tracing.sweep_seconds": tracing.sweep_seconds,
        "tracing.settle_seconds": tracing.settle_seconds,
        "tracing.batch_limit": tracing.batch_limit,
        "tracing.max_lag_seconds": tracing.max_lag_seconds,
        "tracing.replay_max_window": tracing.replay_max_window,
        "tracing.worker_programs": tracing.worker_programs,
        "tracing.worker_program_services": dict(tracing.worker_program_services),
        "tracing.harness_telemetry": tracing.harness_telemetry,
        "tools.path_prepend": list(config.tool_path_prepend),
        "tools.env_passthrough": list(config.tool_env_passthrough),
    }


def _view(wiring: RunnerWiring) -> SettingsView:
    held = wiring.host_settings()
    reader = held.reader
    snapshot: SettingsSnapshot
    provenance: Mapping[str, HostSettingsChange]
    (snapshot, provenance), in_force_revision, in_force_since, failure = held.status(reader.snapshot_with_provenance)
    in_force = InForce(
        revision=in_force_revision,
        since=in_force_since,
        failed=FailureView(revision=failure[0], error=failure[1], since=failure[2]) if failure else None,
    )
    try:
        values = _values(reader.resolve(snapshot))
    except ValueError as exc:
        return SettingsView(revision=snapshot.revision, in_force=in_force, settings=[], settings_error=str(exc))
    rows: list[SettingRow] = []
    for key, lifetime in reader.keys.items():
        entry = snapshot.entries.get(key)
        change = provenance.get(key)
        stated = entry is not None and not entry.at_default
        rows.append(
            SettingRow(
                key=key,
                value=entry.value if entry is not None and stated else values[key],
                source="entry" if stated else "default",
                lifetime=lifetime,
                revision=change.revision if change else None,
                written_at=iso_utc(change.recorded_at) if change else None,
                written_by=change.actor if change else None,
            )
        )
    return SettingsView(revision=snapshot.revision, in_force=in_force, settings=rows)


@router.get("", response_model=SettingsView)
def get_host_settings(request: Request) -> SettingsView:
    return _view(RunnerWiring.of(request))


@router.patch("", response_model=SettingsView, responses=WRITE_RESPONSES)
def patch_host_settings(
    request: Request,
    body: SettingsWrite,
    session: Annotated[RunnerSession, Depends(require_superuser)],
    if_match: Annotated[str | None, Header()] = None,
) -> SettingsView | JSONResponse:
    wiring = RunnerWiring.of(request)
    try:
        revision = int(if_match) if if_match is not None else None
    except ValueError:
        revision = None
    try:
        wiring.host_settings_authoring().edit(
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


@router.get("/changes", response_model=HostChangesPage)
def host_settings_changes(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    before: Annotated[int | None, Query(ge=1)] = None,
    record_kind: str | None = None,
    record_key: str | None = None,
) -> HostChangesPage:
    reader = RunnerWiring.of(request).host_settings().reader
    changes = reader.changes(limit=limit + 1, before=before, record_kind=record_kind, record_key=record_key)
    page = changes[:limit]
    return HostChangesPage(
        changes=[
            HostChangeView(
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
                record_kind=c.record_kind,
                record_key=c.record_key,
            )
            for c in page
        ],
        next_before=page[-1].id if len(changes) > limit else None,
    )


#: A host settings document is the host file's shape: its host keys, and its ``[[runner]]``, ``[[workspace]]`` and
#: ``[[subscription]]`` tables.
HostSettingsDocument = dict[str, object]

#: A document apply's refusals: a superuser is required, and any refused entry fails the whole document.
APPLY_RESPONSES: dict[int | str, dict[str, object]] = {
    403: {"description": "Superuser required"},
    422: {
        "description": "A refused, malformed or invalid document entry; nothing is written",
        "content": {"application/problem+json": SETTINGS_PROBLEM},
    },
}


class AppliedFieldChange(WireModel):
    """One key an apply moves, each side its stated value — ``null`` for absent or at its default. Shaped as the
    hub's apply diff."""

    field: str
    old: object = None
    new: object = None


class HostSettingsOutcome(WireModel):
    """One entry's outcome: ``kind`` ``host``, ``runner``, ``workspace`` or ``subscription``; ``key`` the host key,
    runner id, workspace id or subscription slug; ``op`` ``create``, ``edit`` or ``enable`` for a change written and
    ``unchanged`` for one that needed none."""

    kind: str
    key: str
    op: Annotated[ConfigApplyOp | Unrecognized, OPEN]
    diff: list[AppliedFieldChange] = Field(default_factory=list[AppliedFieldChange])


class HostSettingsApplyResponse(WireModel):
    """What an apply did, or — under ``dry_run`` — would do, with the identical ``outcomes``. ``apply_id`` groups the
    change rows a real apply wrote; ``null`` for a dry run, or for a document that writes nothing."""

    dry_run: bool
    apply_id: str | None = None
    outcomes: list[HostSettingsOutcome]


def _applied_value(entry: Entry | None) -> object:
    return None if entry is None or entry.at_default else entry.value


def _outcome(outcome: DocumentOutcome) -> HostSettingsOutcome:
    return HostSettingsOutcome(
        kind=outcome.kind,
        key=outcome.key,
        op=outcome.op,
        diff=[
            AppliedFieldChange(field=change.key, old=_applied_value(change.old), new=_applied_value(change.new))
            for change in outcome.diff
        ],
    )


@router.post("/apply", response_model=HostSettingsApplyResponse, responses=APPLY_RESPONSES)
def apply_host_settings(
    request: Request,
    document: HostSettingsDocument,
    session: Annotated[RunnerSession, Depends(require_superuser)],
    dry_run: bool = False,
) -> HostSettingsApplyResponse | JSONResponse:
    """Apply a host settings document whole: every entry it states is reconciled in one transaction, and a record it
    omits is left as it stands. ``dry_run`` makes and rolls back the same writes."""
    application = RunnerWiring.of(request).host_settings_application()
    try:
        apply_id, plan = application.apply(document, actor=session.username, dry_run=dry_run)
    except ApplyRefusal as exc:
        return settings_problem(
            422, exc.code, str(exc), key=exc.key, record_kind=exc.record_kind, record_key=exc.record_key
        )
    except ApplyInvalid as exc:
        return settings_problem(
            422,
            "settings_invalid",
            str(exc),
            errors=[{"key": key, "message": message} for key, message in exc.errors.items()],
            record_kind=exc.record_kind,
            record_key=exc.record_key,
        )
    return HostSettingsApplyResponse(dry_run=dry_run, apply_id=apply_id, outcomes=[_outcome(o) for o in plan.outcomes])


@router.get("/export", response_model=HostSettingsDocument)
def export_host_settings(request: Request) -> HostSettingsDocument:
    """The host settings document stating every entry not at its default; applied back, it moves nothing."""
    return RunnerWiring.of(request).host_settings_application().export()
