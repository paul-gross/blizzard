"""The host-facing fleet router — every host->hub call under ``/api/fleet/host*``.

Enforcement is structural: the router's own ``dependencies`` authenticate a host verb *because of where it is mounted*,
and the caller is always the host its bearer token names — a ``host_id`` a request body carries is never read. A host
token resolves no runner and a runner token resolves no host, so each router refuses the other's credential with 401.
Every other ``/api/fleet`` route stays behind the runner principal (:mod:`blizzard.hub.api.fleet`)."""

from __future__ import annotations

from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject_sync
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse

from blizzard.foundation.instants import iso_utc
from blizzard.foundation.logging import get_logger
from blizzard.hub.api import chunk_events, problems
from blizzard.hub.api.auth import HostPrincipal, require_host_principal
from blizzard.hub.api.auth_session import tenant_bound
from blizzard.hub.api.ingest_broadcast import pushed_facts
from blizzard.hub.api.problem_rendering import problem_response
from blizzard.hub.api.runners import (
    Events,
    Fleet,
    Frames,
    HostEnrollment,
    Hosts,
    Routes,
    RunnerReads,
    Runners,
    publish_retirement,
)
from blizzard.hub.domain.execution.fleet import RetireInterrupted, RetireOutcome
from blizzard.hub.domain.execution.host_facts import HostFactIngestService
from blizzard.hub.domain.runners.host import Host, HostReport, HostRetired, HostWorkspace, IReadHostRegistry, origins_of
from blizzard.hub.domain.runners.host_report import HostReportService
from blizzard.hub.domain.runners.registration import (
    DeclaredSubscription,
    IReadRunnerRegistry,
    RunnerHoldsRoutes,
    RunnerLifecycleConflict,
)
from blizzard.hub.domain.runners.served import UnknownServedProject
from blizzard.hub.events.broker import EventBroker
from blizzard.wire.facts import HostFactAck, HostFactBatch
from blizzard.wire.host import HostRecordResponse, HostReportRequest, HostRunnerAddRequest, HostRunnerView
from blizzard.wire.runner import RunnerAddResponse, RunnerRetireRequest, RunnerRetireResponse

_log = get_logger("blizzard.hub.fleet_host")

router = APIRouter(
    prefix="/api/fleet/host", tags=["fleet-host"], dependencies=[Depends(require_host_principal), Depends(tenant_bound)]
)

Report = FromDishka[HostReportService]
HostFacts = FromDishka[HostFactIngestService]


def _held_host(hosts: IReadHostRegistry, principal: HostPrincipal) -> Host | None:
    """The calling host's loaded record — the one its bearer token names; its row vanishing since the gate resolved it
    reads as an unresolved token."""
    return hosts.get_host(principal.host_id)


def _unresolved(request: Request) -> JSONResponse:
    return problem_response(
        problems.authentication_unresolved_problem(instance=request.url.path, detail="bearer token does not resolve"),
    )


def _record(registry: IReadRunnerRegistry, principal: HostPrincipal, host: Host) -> HostRecordResponse:
    runners = registry.list_runners(host_id=host.host_id)
    return HostRecordResponse(
        host_id=host.host_id,
        tenant_id=principal.tenant_id,
        host_name=host.name,
        registered_at=None if host.registered_at is None else iso_utc(host.registered_at),
        last_seen_at=None if host.last_seen_at is None else iso_utc(host.last_seen_at),
        runners=[HostRunnerView(runner_id=r.runner_id, runner_name=r.name) for r in runners],
    )


@router.post("", response_model=HostRecordResponse)
@inject_sync
def report_host(
    request: HostReportRequest,
    hosts: Hosts,
    registry: Runners,
    report: Report,
    events: Events,
    principal: Annotated[HostPrincipal, Depends(require_host_principal)],
    http_request: Request,
) -> HostRecordResponse | JSONResponse:
    """Report the calling host — the one its bearer token names — and answer its record.

    The hub never rejects a report over its content — it doubles as the heartbeat every tick. A ``name`` another live
    host holds is not recorded, and the answer carries the name the hub holds. The ``host-changed`` frame is published
    only when the report changed what the hub held, or found the host offline. A retired host is refused 403."""
    held = _held_host(hosts, principal)
    if held is None:
        return _unresolved(http_request)
    try:
        reported = report.report(
            held,
            HostReport(
                name=request.name,
                url=request.url,
                redirect_uris=tuple(request.redirect_uris),
                urls=origins_of(request.urls, request.url),
                workspaces=tuple(HostWorkspace(w.workspace_id, w.env_capacity) for w in request.workspaces),
                subscriptions=tuple(
                    DeclaredSubscription(slug=d.slug, name=d.name, provider=d.provider) for d in request.subscriptions
                ),
            ),
        )
    except HostRetired as exc:
        return problem_response(problems.host_retired_problem(exc, instance=http_request.url.path))
    if reported.changed:
        events.publish_host_changed(held.host_id, kind="reported", host_name=reported.name)
    refreshed = _held_host(hosts, principal)
    if refreshed is None:
        return _unresolved(http_request)
    return _record(registry, principal, refreshed)


@router.get("", response_model=HostRecordResponse)
@inject_sync
def get_host_record(
    hosts: Hosts,
    registry: Runners,
    principal: Annotated[HostPrincipal, Depends(require_host_principal)],
    http_request: Request,
) -> HostRecordResponse | JSONResponse:
    """The calling host's own record, and the runners the hub holds under it that are not retired."""
    host = _held_host(hosts, principal)
    if host is None:
        return _unresolved(http_request)
    return _record(registry, principal, host)


@router.post("/runners", response_model=RunnerAddResponse, status_code=status.HTTP_201_CREATED)
@inject_sync
def add_host_runner(
    request: HostRunnerAddRequest,
    hosts: Hosts,
    host_enrollment: HostEnrollment,
    events: Events,
    principal: Annotated[HostPrincipal, Depends(require_host_principal)],
    http_request: Request,
) -> RunnerAddResponse | JSONResponse:
    """Add a runner under the calling host, in the host's tenant, as an operator's add would — its id and bearer token
    are minted together and the plaintext token is returned once. An entry of ``projects`` naming no project of the
    tenant is refused 422, and a host retired before the add lands 403; nothing is added."""
    host = _held_host(hosts, principal)
    if host is None:
        return _unresolved(http_request)
    try:
        added = host_enrollment.add_runner(host, request.name, by=host.name, projects=request.projects)
    except UnknownServedProject as exc:
        return problem_response(problems.unknown_served_project_problem(exc, instance=http_request.url.path))
    except HostRetired as exc:
        return problem_response(problems.host_retired_problem(exc, instance=http_request.url.path))
    events.publish_runner_changed(added.runner_id, kind="added", runner_name=added.name, by=host.name)
    return RunnerAddResponse(runner_id=added.runner_id, runner_name=added.name, token=added.token)


@router.post("/runners/{runner_id}/retire", response_model=RunnerRetireResponse)
@inject_sync
def retire_host_runner(
    runner_id: str,
    request: RunnerRetireRequest,
    registry: Runners,
    fleet: Fleet,
    reads: FromDishka[RunnerReads],
    routes: Routes,
    events: Events,
    frames: Frames,
    principal: Annotated[HostPrincipal, Depends(require_host_principal)],
    http_request: Request,
) -> RunnerRetireResponse | JSONResponse:
    """Retire a runner under the calling host, as ``POST /api/runners/{runner_id}/retire`` does — 409 naming each held
    chunk unless ``force``. A runner that does not stand under the calling host is refused as unknown (404), and a
    retire — a re-run included — whose runner was retired or reinstated after it was read is refused 409, revoking no
    token and releasing no route the runner holds since; what it committed first is still published."""
    registration = registry.get_runner(runner_id)
    if registration is None or registration.host_id != principal.host_id:
        return problem_response(problems.runner_not_found_problem(runner_id, instance=http_request.url.path))
    held = routes.live_routes_of_runner(runner_id)
    changes = chunk_events.ChunkChanged.before_many(frames, [r.chunk_id for r in held])
    try:
        outcome: RetireOutcome = fleet.retire(registration, by=principal.host_name, force=request.force)
    except RetireInterrupted as exc:
        publish_retirement(events, frames, registration, exc.committed, by=principal.host_name, changes=changes)
        return problem_response(problems.runner_retire_interrupted_problem(exc, instance=http_request.url.path))
    except RunnerHoldsRoutes as exc:
        return problem_response(problems.runner_holds_routes_problem(exc, instance=http_request.url.path))
    except RunnerLifecycleConflict as exc:
        return problem_response(problems.runner_lifecycle_conflict_problem(exc, instance=http_request.url.path))
    publish_retirement(events, frames, registration, outcome, by=principal.host_name, changes=changes)
    refreshed = registry.get_runner(runner_id)
    assert refreshed is not None  # just retired, so the runner exists

    return RunnerRetireResponse(
        runner=reads.detail(refreshed),
        released_chunk_ids=[r.chunk_id for r in outcome.released],
    )


def _publish_spenders(registry: IReadRunnerRegistry, events: EventBroker, host: Host, slugs: frozenset[str]) -> None:
    """Publish the ``runner_changed(external-usage)`` frame for each live runner under ``host`` spending a slug the
    push wrote."""
    for runner in registry.list_runners(host_id=host.host_id):
        if runner.subscription in slugs:
            events.publish_runner_changed(runner.runner_id, kind="external-usage", runner_name=runner.name)


@router.post("/events", response_model=HostFactAck)
@inject_sync
def ingest_host_facts(
    batch: HostFactBatch,
    hosts: Hosts,
    registry: Runners,
    host_facts: HostFacts,
    events: Events,
    principal: Annotated[HostPrincipal, Depends(require_host_principal)],
    http_request: Request,
) -> HostFactAck | JSONResponse:
    """Land the calling host's own facts — its subscription usage samples and misses — idempotent on the batch's
    per-host ``seq`` high-water mark; 403 when the host is retired. A fact of any other kind is rejected in the ack
    and holds the mark behind it."""
    host = _held_host(hosts, principal)
    if host is None:
        return _unresolved(http_request)
    try:
        result = host_facts.ingest(host, pushed_facts(batch))
    except HostRetired as exc:
        return problem_response(problems.host_retired_problem(exc, instance=http_request.url.path))
    if result.slugs:
        _publish_spenders(registry, events, host, result.slugs)
    return HostFactAck(
        host_id=host.host_id,
        high_water=result.high_water,
        applied=result.applied,
        already_applied=result.already_applied,
        rejected=result.rejected,
    )
