"""The host's workspaces and subscriptions as records — create, list, get, edit, retire and enable on
``/api/host/workspaces`` and ``/api/host/subscriptions``.

Every write takes a superuser session and is judged as the host file's table is at boot before the store writes the
record, its lifecycle fact and its change row; the lists keep their members and add each record's revision, state and
settings. The host tick takes a write up."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse

from blizzard.foundation.settings_record.refusals import SettingsInvalid, SettingsRevisionConflict
from blizzard.foundation.store.utc import iso_utc
from blizzard.foundation.subscription_miss import SampleMissReason
from blizzard.runner.api.federation import require_superuser
from blizzard.runner.api.problems import problem, reject_worker_credential
from blizzard.runner.api.settings import settings_problem
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.auth.session import RunnerSession
from blizzard.runner.config import DEFAULT_MAX_ENVIRONMENTS, SubscriptionDeclaration
from blizzard.runner.host_records.model import HostRecord, RecordExists, RecordKind, RecordUnknown
from blizzard.runner.host_records.service import HostRecordService
from blizzard.runner.hosting.model import API
from blizzard.runner.subscriptions.subscription_sampler import ExternalSubscriptionUsageWindow
from blizzard.runner.usage.repository import IReadExternalUsageRepository
from blizzard.wire.runner import ExternalSubscriptionUsageWindowView
from blizzard.wire.runner_host import (
    HostSubscriptionCreateRequest,
    HostSubscriptionListResponse,
    HostSubscriptionPatchRequest,
    HostSubscriptionView,
    HostWorkspaceCreateRequest,
    HostWorkspaceListResponse,
    HostWorkspacePatchRequest,
    HostWorkspaceView,
)

router = APIRouter(prefix="/api/host", tags=["host"])
writes = APIRouter(prefix="/api/host", tags=["host"], dependencies=[Depends(reject_worker_credential)])

_PROBLEM = {"application/problem+json": {"schema": {"$ref": "#/components/schemas/RunnerProblem"}}}
_SETTINGS_PROBLEM = {"application/problem+json": {"schema": {"$ref": "#/components/schemas/SettingsProblem"}}}
_UNKNOWN: dict[int | str, dict[str, Any]] = {404: {"description": "The host holds no such record", "content": _PROBLEM}}
_SUPERUSER: dict[int | str, dict[str, Any]] = {403: {"description": "Superuser required"}}
_INVALID: dict[int | str, dict[str, Any]] = {
    422: {"description": "The host file would fail at boot with it", "content": _SETTINGS_PROBLEM}
}
_CREATE = {
    **_SUPERUSER,
    409: {"description": "A record of the kind already holds the key", "content": _PROBLEM},
    **_INVALID,
}
_EDIT = {
    **_SUPERUSER,
    **_UNKNOWN,
    409: {"description": "The record is no longer at the If-Match revision", "content": _SETTINGS_PROBLEM},
    **_INVALID,
}
_LIFECYCLE = {
    **_SUPERUSER,
    **_UNKNOWN,
    409: {"description": "Another write moved the record first", "content": _SETTINGS_PROBLEM},
    **_INVALID,
}


def workspace_view(record: HostRecord) -> HostWorkspaceView:
    """A workspace record as the host lists it."""
    cap = record.setting("max_environments", DEFAULT_MAX_ENVIRONMENTS)
    return HostWorkspaceView(
        workspace_id=record.key,
        max_environments=cap if isinstance(cap, int) else DEFAULT_MAX_ENVIRONMENTS,
        revision=record.revision,
        state=record.state(),
        settings=dict(record.settings),
    )


def _listed(wiring: RunnerWiring, kind: RecordKind, *, retired: bool) -> tuple[HostRecord, ...] | None:
    """The host's ``kind`` records, the retired ones too when ``retired``, read at once; ``None`` for a host whose
    store holds none of the kind, retired or not, so it lists what it serves — one built without the host's store,
    or never seeded."""
    stores = wiring.maybe_host_read_stores()
    if stores is None:
        return None
    every = stores.records_of(kind).records(retired=True)
    if not every:
        return None
    return every if retired else tuple(record for record in every if not record.retired)


def _record(wiring: RunnerWiring, kind: RecordKind, key: str) -> HostRecord | JSONResponse:
    stores = wiring.host_read_stores()
    record = stores.records_of(kind).record(key)
    if record is None:
        return _unknown(RecordUnknown(kind, key))
    return record


def _unknown(exc: RecordUnknown) -> JSONResponse:
    return problem(404, f"{exc.kind}_unknown", str(exc))


def _revision(if_match: str | None) -> int | None:
    try:
        return int(if_match) if if_match is not None else None
    except ValueError:
        return None


def record_write[V](
    write: Callable[[HostRecordService], HostRecord], service: HostRecordService, view: Callable[[HostRecord], V]
) -> V | JSONResponse:
    """``write`` through ``service``, viewed by ``view``; each refusal its problem."""
    try:
        return view(write(service))
    except RecordUnknown as exc:
        return _unknown(exc)
    except RecordExists as exc:
        return problem(409, f"{exc.kind}_exists", str(exc))
    except SettingsRevisionConflict as exc:
        return settings_problem(409, "revision_conflict", str(exc), current=exc.current)
    except SettingsInvalid as exc:
        return settings_problem(
            422,
            "settings_invalid",
            str(exc),
            errors=[{"key": key, "message": message} for key, message in exc.errors.items()],
        )


# --- Workspaces -------------------------------------------------------------


@router.get("/workspaces", response_model=HostWorkspaceListResponse)
def list_workspaces(request: Request, retired: bool = False) -> HostWorkspaceListResponse:
    """Every workspace the host holds, each with its record's revision, state and settings; ``?retired=true`` lists
    the retired ones beside the active."""
    wiring = RunnerWiring.of(request)
    records = _listed(wiring, RecordKind.WORKSPACE, retired=retired)
    if records is None:
        return HostWorkspaceListResponse(
            items=[
                HostWorkspaceView(workspace_id=w.workspace_id, max_environments=w.max_environments)
                for w in wiring.workspaces()
            ]
        )
    return HostWorkspaceListResponse(items=[workspace_view(r) for r in records])


@router.get("/workspaces/{workspace_id}", response_model=HostWorkspaceView, responses=_UNKNOWN)
def get_workspace(request: Request, workspace_id: str) -> HostWorkspaceView | JSONResponse:
    """One workspace record, retired or not."""
    record = _record(RunnerWiring.of(request), RecordKind.WORKSPACE, workspace_id)
    return record if isinstance(record, JSONResponse) else workspace_view(record)


@writes.post("/workspaces", response_model=HostWorkspaceView, responses=_CREATE)
def create_workspace(
    request: Request,
    body: HostWorkspaceCreateRequest,
    session: Annotated[RunnerSession, Depends(require_superuser)],
) -> HostWorkspaceView | JSONResponse:
    """Create a workspace record at revision 1, judged as the host file's ``[[workspace]]`` is at boot."""
    sets, _ = body.stated()
    return record_write(
        lambda service: service.create(body.id, sets, actor=session.username, door=API),
        RunnerWiring.of(request).host_records(RecordKind.WORKSPACE),
        workspace_view,
    )


@writes.patch("/workspaces/{workspace_id}", response_model=HostWorkspaceView, responses=_EDIT)
def edit_workspace(
    request: Request,
    workspace_id: str,
    body: HostWorkspacePatchRequest,
    session: Annotated[RunnerSession, Depends(require_superuser)],
    if_match: Annotated[str | None, Header()] = None,
) -> HostWorkspaceView | JSONResponse:
    """Sparse-merge ``body`` into the workspace at its ``If-Match`` revision; a retired one stays retired."""
    sets, unsets = body.stated()
    return record_write(
        lambda service: service.edit(
            service.held(workspace_id),
            sets,
            unsets,
            if_match=_revision(if_match),
            actor=session.username,
            door=API,
        ),
        RunnerWiring.of(request).host_records(RecordKind.WORKSPACE),
        workspace_view,
    )


@writes.post("/workspaces/{workspace_id}/retire", response_model=HostWorkspaceView, responses=_LIFECYCLE)
def retire_workspace(
    request: Request, workspace_id: str, session: Annotated[RunnerSession, Depends(require_superuser)]
) -> HostWorkspaceView | JSONResponse:
    """Retire the workspace; of a retired one, writes nothing."""
    return record_write(
        lambda service: service.retire(service.held(workspace_id), actor=session.username, door=API),
        RunnerWiring.of(request).host_records(RecordKind.WORKSPACE),
        workspace_view,
    )


@writes.post("/workspaces/{workspace_id}/enable", response_model=HostWorkspaceView, responses=_LIFECYCLE)
def enable_workspace(
    request: Request, workspace_id: str, session: Annotated[RunnerSession, Depends(require_superuser)]
) -> HostWorkspaceView | JSONResponse:
    """Reverse the workspace's retirement; of an enabled one, writes nothing."""
    return record_write(
        lambda service: service.enable(service.held(workspace_id), actor=session.username, door=API),
        RunnerWiring.of(request).host_records(RecordKind.WORKSPACE),
        workspace_view,
    )


# --- Subscriptions ----------------------------------------------------------


def _spenders(wiring: RunnerWiring) -> dict[str, list[str]]:
    spenders: dict[str, list[str]] = {}
    for served in wiring.runners().served:
        for declaration in wiring.for_runner(served).config().resolved_subscriptions():
            spenders.setdefault(declaration.slug, []).append(served.runner_id)
    return spenders


@router.get("/subscriptions", response_model=HostSubscriptionListResponse)
def list_subscriptions(request: Request, retired: bool = False) -> HostSubscriptionListResponse:
    """Every subscription the host holds, each with its own newest sampling attempt and credential renewal, the
    runners that spend it, and its record's revision, state and settings; ``?retired=true`` lists the retired ones
    beside the active."""
    wiring = RunnerWiring.of(request)
    records = _listed(wiring, RecordKind.SUBSCRIPTION, retired=retired)
    listed: Sequence[SubscriptionDeclaration | HostRecord] = records if records is not None else wiring.subscriptions()
    return subscription_list(listed, wiring.host_read_stores().external_usage, _spenders(wiring))


@router.get("/subscriptions/{slug}", response_model=HostSubscriptionView, responses=_UNKNOWN)
def get_subscription(request: Request, slug: str) -> HostSubscriptionView | JSONResponse:
    """One subscription record, retired or not, with its diagnostics."""
    wiring = RunnerWiring.of(request)
    record = _record(wiring, RecordKind.SUBSCRIPTION, slug)
    return record if isinstance(record, JSONResponse) else _subscription_view(wiring, record)


def _subscription_view(wiring: RunnerWiring, record: HostRecord) -> HostSubscriptionView:
    return subscription_list([record], wiring.host_read_stores().external_usage, _spenders(wiring)).items[0]


@writes.post("/subscriptions", response_model=HostSubscriptionView, responses=_CREATE)
def create_subscription(
    request: Request,
    body: HostSubscriptionCreateRequest,
    session: Annotated[RunnerSession, Depends(require_superuser)],
) -> HostSubscriptionView | JSONResponse:
    """Create a subscription record at revision 1, judged as the host file's ``[[subscription]]`` is at boot."""
    wiring = RunnerWiring.of(request)
    sets, _ = body.stated()
    return record_write(
        lambda service: service.create(body.slug, sets, actor=session.username, door=API),
        wiring.host_records(RecordKind.SUBSCRIPTION),
        lambda record: _subscription_view(wiring, record),
    )


@writes.patch("/subscriptions/{slug}", response_model=HostSubscriptionView, responses=_EDIT)
def edit_subscription(
    request: Request,
    slug: str,
    body: HostSubscriptionPatchRequest,
    session: Annotated[RunnerSession, Depends(require_superuser)],
    if_match: Annotated[str | None, Header()] = None,
) -> HostSubscriptionView | JSONResponse:
    """Sparse-merge ``body`` into the subscription at its ``If-Match`` revision; a retired one stays retired."""
    wiring = RunnerWiring.of(request)
    sets, unsets = body.stated()
    return record_write(
        lambda service: service.edit(
            service.held(slug), sets, unsets, if_match=_revision(if_match), actor=session.username, door=API
        ),
        wiring.host_records(RecordKind.SUBSCRIPTION),
        lambda record: _subscription_view(wiring, record),
    )


@writes.post("/subscriptions/{slug}/retire", response_model=HostSubscriptionView, responses=_LIFECYCLE)
def retire_subscription(
    request: Request, slug: str, session: Annotated[RunnerSession, Depends(require_superuser)]
) -> HostSubscriptionView | JSONResponse:
    """Retire the subscription; of a retired one, writes nothing. Its samples and usage-limit marks stay."""
    wiring = RunnerWiring.of(request)
    return record_write(
        lambda service: service.retire(service.held(slug), actor=session.username, door=API),
        wiring.host_records(RecordKind.SUBSCRIPTION),
        lambda record: _subscription_view(wiring, record),
    )


@writes.post("/subscriptions/{slug}/enable", response_model=HostSubscriptionView, responses=_LIFECYCLE)
def enable_subscription(
    request: Request, slug: str, session: Annotated[RunnerSession, Depends(require_superuser)]
) -> HostSubscriptionView | JSONResponse:
    """Reverse the subscription's retirement; of an enabled one, writes nothing."""
    wiring = RunnerWiring.of(request)
    return record_write(
        lambda service: service.enable(service.held(slug), actor=session.username, door=API),
        wiring.host_records(RecordKind.SUBSCRIPTION),
        lambda record: _subscription_view(wiring, record),
    )


def subscription_list(
    listed: Sequence[SubscriptionDeclaration | HostRecord],
    usage: IReadExternalUsageRepository,
    spenders: dict[str, list[str]],
) -> HostSubscriptionListResponse:
    """Each of ``listed`` with its diagnostics, read for all of them at once."""
    slugs = [item.key if isinstance(item, HostRecord) else item.slug for item in listed]
    attempts = usage.latest_external_usage_attempts_by_slug(slugs)
    renewals = usage.latest_credential_renewals_by_slug(slugs)
    samples = usage.latest_external_usage_samples_by_slug(slugs)
    items: list[HostSubscriptionView] = []
    for slug, item in zip(slugs, listed, strict=True):
        attempt = attempts.get(slug)
        renewal = renewals.get(slug)
        sample = samples.get(slug)
        record: HostRecord | None
        if isinstance(item, HostRecord):
            record, name, provider = item, str(item.setting("name", "")), str(item.setting("provider", ""))
        else:
            record, name, provider = None, item.name, item.provider
        items.append(
            HostSubscriptionView(
                slug=slug,
                name=name,
                provider=provider,
                sampled_at=iso_utc(attempt.sampled_at) if attempt is not None else None,
                ok=attempt.ok if attempt is not None else None,
                miss_reason=SampleMissReason.recognized(attempt.miss_reason) if attempt is not None else None,
                renewal_attempted_at=iso_utc(renewal.attempted_at) if renewal is not None else None,
                renewal_result=renewal.result if renewal is not None else None,
                renewal_failure_reason=renewal.failure_reason if renewal is not None else None,
                spenders=spenders.get(slug, []),
                windows=[_window_view(w) for w in sample.windows] if sample is not None else [],
                windows_sampled_at=iso_utc(sample.sampled_at) if sample is not None else None,
                condition=attempt.condition() if attempt is not None else None,
                revision=record.revision if record is not None else None,
                state=record.state() if record is not None else "active",
                settings=dict(record.settings) if record is not None else {},
            )
        )
    return HostSubscriptionListResponse(items=items)


def _window_view(window: ExternalSubscriptionUsageWindow) -> ExternalSubscriptionUsageWindowView:
    return ExternalSubscriptionUsageWindowView(
        window=window.window,
        utilization_pct=window.utilization_pct,
        resets_at=iso_utc(window.resets_at),
        window_seconds=window.window_seconds,
    )
