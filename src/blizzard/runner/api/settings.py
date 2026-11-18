"""Human access to one runner's store-backed settings record."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from blizzard.foundation.config_keys import Lifetime
from blizzard.foundation.settings_record.refusals import SettingsInvalid, SettingsRevisionConflict
from blizzard.foundation.settings_record.vocabulary import Entry
from blizzard.foundation.store.utc import iso_utc
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.runner.api.federation import require_superuser
from blizzard.runner.api.lease_token import presented_lease_token
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.auth.session import RunnerSession
from blizzard.runner.config import RunnerConfig
from blizzard.runner.config_keys import HOST_REGISTRY
from blizzard.runner.settings.policy import SettingRefusal
from blizzard.runner.settings.ports import SettingsChange, SettingsSnapshot
from blizzard.runner.throttle.settings import DAY_NAMES, DEFAULT_BUDGET_WINDOW_HOURS, DEFAULT_PACE_LEAD_PCT
from blizzard.wire.base import WireModel
from blizzard.wire.vocabulary import OPEN


def _reject_worker_credential(request: Request) -> None:
    # An ungated host grants implicit human authority, but a presented lease token
    # identifies the worker lane, not an implicit human caller.
    if presented_lease_token(request) is not None:
        raise HTTPException(status_code=401, detail="runner session required")


router = APIRouter(prefix="/api/settings", tags=["runner settings"], dependencies=[Depends(_reject_worker_credential)])


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


class SettingsProblem(WireModel):
    type: str
    title: str
    status: int
    detail: str
    code: str
    key: str | None = None
    current: int | None = None
    errors: list[SettingError] | None = None


_SETTINGS_PROBLEM = {"schema": {"$ref": "#/components/schemas/SettingsProblem"}}
_VALIDATION_ERROR = {"schema": {"$ref": "#/components/schemas/HTTPValidationError"}}


def _problem(status: int, code: str, message: str, **members: object) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=SettingsProblem.model_validate(
            {
                "type": f"urn:blizzard:problem:{code.replace('_', '-')}",
                "title": code.replace("_", " "),
                "status": status,
                "detail": message,
                "code": code,
                **members,
            }
        ).model_dump(mode="json"),
        media_type="application/problem+json",
    )


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
        resolved = service.resolve_for_read(snapshot.entries)
    except Exception as exc:
        return SettingsView(
            revision=snapshot.revision, in_force=in_force, settings=[], settings_error=str(exc) or type(exc).__name__
        )
    defaults = _resolved_values(resolved)
    rows: list[SettingRow] = []
    keys = dict(service.keys)
    keys.update(
        {
            k.path.removeprefix("runner[]."): k.lifetime
            for k in HOST_REGISTRY.keys
            if k.path.startswith("runner[].") and k.lifetime == Lifetime.IMMUTABLE and not k.deprecated
        }
    )
    for key, lifetime in keys.items():
        entry = snapshot.entries.get(key)
        change = provenance.get(key)
        # A legacy file spelling is still an entry for its replacement until
        # that replacement is explicitly set or unset. Project its parsed
        # value rather than the legacy representation.
        alias = next(
            (
                a
                for a, replacement in service.aliases.items()
                if replacement == key and a != "harness_permission_mode" and a in snapshot.entries
            ),
            None,
        )
        if entry is None and alias is not None:
            entry = snapshot.entries[alias]
            change = provenance.get(alias)
            value = defaults[key]
        else:
            value = entry.value if entry is not None and not entry.at_default else defaults[key]
        if key == "id":
            value = service.entry.id
        elif key == "name" and (entry is None or entry.at_default):
            value = service.entry.name
        rows.append(
            SettingRow(
                key=key,
                value=value,
                source="entry" if entry is not None and not entry.at_default else "default",
                lifetime=lifetime,
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


def _resolved_values(config: RunnerConfig) -> dict[str, object]:
    """Project parser-owned defaults in the record's wire vocabulary."""
    throttle = config.throttle
    pace = throttle.pace if throttle else None
    schedule = throttle.schedule if throttle else None
    budget = throttle.budget if throttle else None
    utilization = throttle.utilization if throttle else None
    return {
        "id": config.runner_id,
        "name": config.name,
        "subscription": config.subscriptions[0].slug if config.subscriptions else None,
        "projects": [{"project": b.project, "workspace": b.workspace} for b in config.bindings or ()],
        "agents": config.agents,
        "gates": list(config.gates),
        "runner_prompt": config.runner_prompt,
        "runner_prompt_file": config.runner_prompt_file,
        "transcripts_root": config.transcripts_root,
        "harness.autonomy": config.autonomy.value,
        "harness.config_dir": str(config.harness_config_dir) if config.harness_config_dir else None,
        "worker.env_passthrough": list(config.worker_env_passthrough),
        "worker.path_prepend": list(config.worker_path_prepend),
        "worker.shell": config.worker_shell,
        "queue.strict": config.queue_strict,
        "cost.chunk_cap_usd": config.chunk_cap_usd,
        "cost.runner_ceiling_usd": config.runner_ceiling_usd,
        "cost.window_hours": config.runner_ceiling_window_hours,
        "throttle.pace.window": pace.window if pace else None,
        "throttle.pace.lead_pct": pace.lead_pct if pace else DEFAULT_PACE_LEAD_PCT,
        "throttle.pace.taper": [{"from_pct": step.from_pct, "lead_pct": step.lead_pct} for step in pace.taper]
        if pace
        else [],
        "throttle.schedule.days": [DAY_NAMES[day] for day in schedule.days] if schedule else None,
        "throttle.schedule.hours": [
            f"{span.start_minute // 60:02d}:{span.start_minute % 60:02d}-"
            f"{span.end_minute // 60:02d}:{span.end_minute % 60:02d}"
            for span in schedule.spans
        ]
        if schedule
        else None,
        "throttle.utilization.below_pct": utilization.below_pct if utilization else None,
        "throttle.utilization.windows": utilization.windows if utilization else None,
        "throttle.budget.usd": budget.usd if budget else None,
        "throttle.budget.window_hours": budget.window_hours if budget else DEFAULT_BUDGET_WINDOW_HOURS,
        "throttle.on_decline": throttle.on_decline.value if throttle else "hold",
        "throttle.on_usage_limit": throttle.on_usage_limit.value if throttle else "hold",
        "context.warn_tokens": config.context_warn_tokens,
        "context.sample_interval_seconds": config.context_sample_interval_seconds,
        **{key: value for section in config.harness_sections for key, value in section.settings_values().items()},
    }


@router.get("", response_model=SettingsView)
def get_settings(request: Request) -> SettingsView:
    return _view(RunnerWiring.of(request))


@router.patch(
    "",
    response_model=SettingsView,
    responses={
        403: {"description": "Superuser required"},
        409: {
            "description": "Settings revision conflict",
            "content": {"application/problem+json": _SETTINGS_PROBLEM},
        },
        422: {
            "description": "Invalid settings edit or request",
            "content": {"application/problem+json": _SETTINGS_PROBLEM, "application/json": _VALIDATION_ERROR},
        },
    },
)
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
        return _problem(422, exc.code, str(exc), key=exc.key)
    except SettingsRevisionConflict as exc:
        return _problem(409, "revision_conflict", str(exc), current=exc.current)
    except SettingsInvalid as exc:
        return _problem(
            422,
            "settings_invalid",
            str(exc),
            errors=[{"key": key, "message": message} for key, message in exc.errors.items()],
        )
    return _view(wiring)


def _entry(value: Entry | None) -> EntryView | None:
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
                        key=d.key, old=_entry(d.old), new=EntryView(value=d.new.value, at_default=d.new.at_default)
                    )
                    for d in c.diff
                ],
            )
            for c in page
        ],
        next_before=page[-1].id if len(changes) > limit else None,
    )
