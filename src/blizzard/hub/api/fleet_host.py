"""The host-facing fleet router — every host->hub call under ``/api/fleet/host*``.

Enforcement is structural: the router's own ``dependencies`` authenticate a host verb *because of where it is mounted*,
and the caller is always the host its bearer token names — a ``host_id`` a request body carries is never read. A host
token resolves no runner and a runner token resolves no host, so each router refuses the other's credential with 401.
Every other ``/api/fleet`` route stays behind the runner principal (:mod:`blizzard.hub.api.fleet`)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api import chunk_events
from blizzard.hub.api.auth import HostPrincipal, require_host_principal
from blizzard.hub.api.auth_session import tenant_bound
from blizzard.hub.api.deps import Services
from blizzard.hub.api.runners import hosts_of, publish_retirement, registry_view, served_projects
from blizzard.hub.composition import TenantServices
from blizzard.hub.domain.execution.fleet import RetireOutcome
from blizzard.hub.domain.runners.host import Host, HostReport, HostRetired, HostWorkspace
from blizzard.hub.domain.runners.registration import DeclaredSubscription, RunnerHoldsRoutes
from blizzard.hub.domain.runners.served import UnknownServedProject
from blizzard.wire.host import HostRecordResponse, HostReportRequest, HostRunnerAddRequest, HostRunnerView
from blizzard.wire.runner import RunnerAddResponse, RunnerRetireRequest, RunnerRetireResponse

_log = get_logger("blizzard.hub.fleet_host")

router = APIRouter(
    prefix="/api/fleet/host", tags=["fleet-host"], dependencies=[Depends(require_host_principal), Depends(tenant_bound)]
)


def _held_host(services: TenantServices, principal: HostPrincipal) -> Host:
    """The calling host's loaded record — the one its bearer token names; its row vanishing since the gate resolved it
    reads as an unresolved token."""
    host = services.hosts.get_host(principal.host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bearer token does not resolve")
    return host


def _record(services: TenantServices, principal: HostPrincipal, host: Host) -> HostRecordResponse:
    runners = services.registry.list_runners(host_id=host.host_id)
    return HostRecordResponse(
        host_id=host.host_id,
        tenant_id=principal.tenant_id,
        host_name=host.name,
        registered_at=None if host.registered_at is None else iso_utc(host.registered_at),
        last_seen_at=None if host.last_seen_at is None else iso_utc(host.last_seen_at),
        runners=[HostRunnerView(runner_id=r.runner_id, runner_name=r.name) for r in runners],
    )


@router.post("", response_model=HostRecordResponse)
def report_host(
    request: HostReportRequest, services: Services, principal: Annotated[HostPrincipal, Depends(require_host_principal)]
) -> HostRecordResponse:
    """Report the calling host — the one its bearer token names — and answer its record.

    The hub never rejects a report over its content — it doubles as the heartbeat every tick. A ``name`` another live
    host holds is not recorded, and the answer carries the name the hub holds. The ``host-changed`` frame is published
    only when the report changed what the hub held, or found the host offline. A retired host is refused 403."""
    held = _held_host(services, principal)
    reported = services.host_report.report(
        held,
        HostReport(
            name=request.name,
            url=request.url,
            redirect_uris=tuple(request.redirect_uris),
            workspaces=tuple(HostWorkspace(w.workspace_id, w.env_capacity) for w in request.workspaces),
            subscriptions=tuple(
                DeclaredSubscription(slug=d.slug, name=d.name, provider=d.provider) for d in request.subscriptions
            ),
        ),
    )
    if reported.changed:
        services.events.publish_host_changed(held.host_id, kind="reported", host_name=reported.name)
    return _record(services, principal, _held_host(services, principal))


@router.get("", response_model=HostRecordResponse)
def get_host_record(
    services: Services, principal: Annotated[HostPrincipal, Depends(require_host_principal)]
) -> HostRecordResponse:
    """The calling host's own record, and the runners the hub holds under it that are not retired."""
    return _record(services, principal, _held_host(services, principal))


@router.post("/runners", response_model=RunnerAddResponse, status_code=status.HTTP_201_CREATED)
def add_host_runner(
    request: HostRunnerAddRequest,
    services: Services,
    principal: Annotated[HostPrincipal, Depends(require_host_principal)],
) -> RunnerAddResponse:
    """Add a runner under the calling host, in the host's tenant, as an operator's add would — its id and bearer token
    are minted together and the plaintext token is returned once. An entry of ``projects`` naming no project of the
    tenant is refused 422, and nothing is added."""
    host = _held_host(services, principal)
    try:
        added = services.host_enrollment.add_runner(host, request.name, by=host.name, projects=request.projects)
    except UnknownServedProject as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except HostRetired as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    services.events.publish_runner_changed(added.runner_id, kind="added", runner_name=added.name, by=host.name)
    return RunnerAddResponse(runner_id=added.runner_id, runner_name=added.name, token=added.token)


@router.post("/runners/{runner_id}/retire", response_model=RunnerRetireResponse)
def retire_host_runner(
    runner_id: str,
    request: RunnerRetireRequest,
    services: Services,
    principal: Annotated[HostPrincipal, Depends(require_host_principal)],
) -> RunnerRetireResponse:
    """Retire a runner under the calling host, as ``POST /api/runners/{runner_id}/retire`` does — 409 naming each held
    chunk unless ``force``. A runner that does not stand under the calling host is refused as unknown (404)."""
    registration = services.registry.get_runner(runner_id)
    if registration is None or registration.host_id != principal.host_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown runner {runner_id}")
    held = services.chunks.route.live_routes_of_runner(runner_id)
    changes = chunk_events.ChunkChanged.before_many(services, [r.chunk_id for r in held])
    try:
        outcome: RetireOutcome = services.fleet.retire(registration, by=request.by, force=request.force)
    except RunnerHoldsRoutes as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    publish_retirement(services, registration, outcome, by=request.by, changes=changes)
    refreshed = services.registry.get_runner(runner_id)
    assert refreshed is not None  # just retired, so the runner exists

    return RunnerRetireResponse(
        runner=registry_view(
            services.fleet.get_liveness(refreshed),
            now=services.clock.now(),
            projects=served_projects(services, refreshed.served),
            hosts=hosts_of(services, [refreshed]),
        ),
        released_chunk_ids=[r.chunk_id for r in outcome.released],
    )
