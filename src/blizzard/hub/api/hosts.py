"""Host routes — the **operator** half of the host registry: add, list, read, adopt, release, enroll, revoke a token,
and retire. Every verb addresses a host by its hub-minted id and never resolves a name. The host-authenticated half is
:mod:`blizzard.hub.api.fleet_host`.

Controllers stay read-only over the store (``bzh:controller-read-only``); a host's bearer token is no session, so the
permission gate refuses it."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from blizzard.auth_core import Permission
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api import chunk_events
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.deps import Services
from blizzard.hub.api.runners import publish_retirement, usage_views
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.composition import TenantServices
from blizzard.hub.domain.execution.host_retirement import HostRetirementInterrupted, HostRetireOutcome
from blizzard.hub.domain.runners.host import (
    Host,
    HostHoldsRoutes,
    HostNameTaken,
    HostNotEnrolled,
    HostRetired,
    HostUsage,
    RunnerAlreadyHosted,
    RunnerNotUnderHost,
    UnknownRunner,
)
from blizzard.hub.domain.runners.registration import PerSubscriptionUsageView, RunnerRegistration, RunnerRetired
from blizzard.wire.host import (
    HostAddRequest,
    HostAddResponse,
    HostEnrollmentResponse,
    HostLifecycleRequest,
    HostRegistryListResponse,
    HostRegistryView,
    HostRetireRequest,
    HostRetireResponse,
    HostRunnersRequest,
    HostSubscriptionUsageView,
    HostTokenRevocationResponse,
    HostWorkspace,
)
from blizzard.wire.runner import RunnerSubscriptionDeclaration

router = APIRouter(
    prefix="/api", tags=["hosts"], dependencies=[Depends(reject_runner_principal), Depends(tenant_bound)]
)


def _iso_or_none(value: datetime | None) -> str | None:
    return iso_utc(value) if value is not None else None


def _subscription_usage(
    host: Host, usage: HostUsage, under: list[RunnerRegistration], *, now: datetime
) -> list[HostSubscriptionUsageView]:
    """Each subscription the host reports, once, through the rules a runner's card follows with the host's reported
    roster as the declaration — so the two agree — naming the live runners under the host spending its slug."""
    spending: dict[str, list[str]] = {}
    for runner in under:
        if not runner.retired and runner.subscription is not None:
            spending.setdefault(runner.subscription, []).append(runner.runner_id)
    views = PerSubscriptionUsageView.over(usage.samples, usage.misses, host.subscriptions, now=now)
    return [
        HostSubscriptionUsageView(**wire.model_dump(), runners=spending.get(wire.slug, []))
        for wire in usage_views(views)
    ]


def registry_view(services: TenantServices, host: Host, usage: HostUsage | None = None) -> HostRegistryView:
    """One host as the operator registry shows it, the runners standing under it read alongside. ``usage`` is the
    host's reported usage when the caller read it in bulk across several hosts, else it is read here."""
    now = services.clock.now()
    under = services.registry.list_runners(include_retired=True, host_id=host.host_id)
    if usage is None:
        usage = services.hosts.usage_of([host.host_id])[host.host_id]
    return HostRegistryView(
        host_id=host.host_id,
        host_name=host.name,
        connection=host.connection(now=now),
        online=host.online(now=now),
        added_at=iso_utc(host.added_at),
        added_by=host.added_by,
        registered_at=_iso_or_none(host.registered_at),
        last_seen_at=_iso_or_none(host.last_seen_at),
        url=host.url,
        redirect_uris=list(host.redirect_uris),
        workspaces=[HostWorkspace(workspace_id=w.workspace_id, env_capacity=w.env_capacity) for w in host.workspaces],
        subscriptions=[
            RunnerSubscriptionDeclaration(slug=d.slug, name=d.name, provider=d.provider) for d in host.subscriptions
        ],
        enrolled=host.token_hash is not None,
        retired=host.retired,
        retired_at=_iso_or_none(host.retired_at),
        retired_by=host.retired_by,
        runner_ids=[r.runner_id for r in under],
        subscription_usage=_subscription_usage(host, usage, under, now=now),
    )


def _host(services: TenantServices, host_id: str) -> Host:
    """The host's loaded record, 404 on an unknown one."""
    host = services.hosts.get_host(host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown host {host_id}")
    return host


def _reread(services: TenantServices, host_id: str) -> HostRegistryView:
    """The host read back after a write, so the response reports what the write set."""
    return registry_view(services, _host(services, host_id))


def _conflict(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


def _publish_host_retirement(
    services: TenantServices,
    host: Host,
    under: list[RunnerRegistration],
    outcome: HostRetireOutcome,
    *,
    by: str,
    changes: dict[str, chunk_events.ChunkChanged],
) -> None:
    """The frames for what a host retire committed — each runner it retired, and the host's own fact."""
    for registration in under:
        if registration.runner_id in outcome.runners:
            publish_retirement(services, registration, outcome.runners[registration.runner_id], by=by, changes=changes)
    if outcome.fact_id is not None:
        services.events.publish_host_changed(
            host.host_id, kind="retired", host_name=host.name, by=by, key=f"host_lifecycle_facts:{outcome.fact_id}"
        )


def _unknown_runner(exc: UnknownRunner | RunnerNotUnderHost) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown runner {exc.runner_id}")


@router.post("/hosts", response_model=HostAddResponse, status_code=status.HTTP_201_CREATED)
def add_host(
    request: HostAddRequest,
    services: Services,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.RUNNER_RETIRE))],
) -> HostAddResponse:
    """Add a host under ``name`` — the hub mints its id and bearer token together, and the plaintext token is returned
    once. ``adopt`` places existing runners under it in the same write: a runner of another tenant is refused as
    unknown (404), one already under a host or a retired one 409, and nothing is added. A name a live host holds is
    refused 409."""
    try:
        added = services.host_enrollment.add(request.name, by=identity.username, adopt=request.adopt)
    except HostNameTaken as exc:
        raise _conflict(exc) from exc
    except UnknownRunner as exc:
        raise _unknown_runner(exc) from exc
    except (RunnerAlreadyHosted, RunnerRetired) as exc:
        raise _conflict(exc) from exc
    services.events.publish_host_changed(added.host_id, kind="added", host_name=added.name, by=identity.username)
    return HostAddResponse(host_id=added.host_id, host_name=added.name, token=added.token, adopted=list(added.adopted))


@router.get("/hosts", response_model=HostRegistryListResponse, dependencies=[Depends(require(Permission.FLEET_VIEW))])
def list_hosts(services: Services, include_retired: Annotated[bool, Query()] = False) -> HostRegistryListResponse:
    """The host registry — every added host, oldest first; a retired host excluded by default, included and marked when
    ``include_retired``. Not paged: a tenant has a handful of hosts, and callers read the whole roster."""
    hosts = services.hosts.list_hosts(include_retired=include_retired)
    usage = services.hosts.usage_of([h.host_id for h in hosts])
    return HostRegistryListResponse(hosts=[registry_view(services, h, usage[h.host_id]) for h in hosts])


@router.get("/hosts/{host_id}", response_model=HostRegistryView, dependencies=[Depends(require(Permission.FLEET_VIEW))])
def get_host(host_id: str, services: Services) -> HostRegistryView:
    """One host's connection condition and the runners under it — the operator's detail read. 404 on unknown."""
    return _reread(services, host_id)


@router.post(
    "/hosts/{host_id}/adoptions",
    response_model=HostRegistryView,
    dependencies=[Depends(require(Permission.RUNNER_RETIRE))],
)
def adopt_runners(host_id: str, request: HostRunnersRequest, services: Services) -> HostRegistryView:
    """Place existing runners under the host, all or none. A runner of another tenant is refused as unknown (404), one
    already under a host or a retired one 409, and so is a retired host."""
    host = _host(services, host_id)
    try:
        services.host_enrollment.adopt(host, request.runner_ids, by=request.by)
    except UnknownRunner as exc:
        raise _unknown_runner(exc) from exc
    except (RunnerAlreadyHosted, RunnerRetired, HostRetired) as exc:
        raise _conflict(exc) from exc
    services.events.publish_host_changed(host_id, kind="adopted", host_name=host.name, by=request.by)
    return _reread(services, host_id)


@router.post(
    "/hosts/{host_id}/releases",
    response_model=HostRegistryView,
    dependencies=[Depends(require(Permission.RUNNER_RETIRE))],
)
def release_runners(host_id: str, request: HostRunnersRequest, services: Services) -> HostRegistryView:
    """Return runners under the host to standing alone, keeping their ids and tokens, all or none. A runner not under
    the host is refused as unknown (404)."""
    host = _host(services, host_id)
    try:
        services.host_enrollment.release(host, request.runner_ids, by=request.by)
    except (UnknownRunner, RunnerNotUnderHost) as exc:
        raise _unknown_runner(exc) from exc
    services.events.publish_host_changed(host_id, kind="released", host_name=host.name, by=request.by)
    return _reread(services, host_id)


@router.post(
    "/hosts/{host_id}/enrollments",
    response_model=HostEnrollmentResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(Permission.RUNNER_RETIRE))],
)
def enroll_host(host_id: str, services: Services) -> HostEnrollmentResponse:
    """Rotate ``host_id``'s bearer token, or issue one after a revocation — the plaintext is returned once, and the
    token it replaces stops resolving. A retired host is refused 409."""
    host = _host(services, host_id)
    try:
        token = services.host_enrollment.enroll(host)
    except HostRetired as exc:
        raise _conflict(exc) from exc
    services.events.publish_host_changed(host_id, kind="token-enrolled", host_name=host.name)
    return HostEnrollmentResponse(host_id=host_id, token=token)


@router.post(
    "/hosts/{host_id}/token-revocations",
    response_model=HostTokenRevocationResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(Permission.RUNNER_RETIRE))],
)
def revoke_host_token(host_id: str, request: HostLifecycleRequest, services: Services) -> HostTokenRevocationResponse:
    """Revoke a host's token and issue none — it stays added and must be enrolled afresh; 409 when unenrolled."""
    host = _host(services, host_id)
    try:
        revocation_id = services.host_enrollment.revoke_token(host, by=request.by)
    except HostNotEnrolled as exc:
        raise _conflict(exc) from exc
    services.events.publish_host_changed(
        host_id,
        kind="token-revoked",
        host_name=host.name,
        by=request.by,
        key=f"host_token_revocations:{revocation_id}",
    )
    return HostTokenRevocationResponse(host=_reread(services, host_id))


@router.post(
    "/hosts/{host_id}/retire",
    response_model=HostRetireResponse,
    dependencies=[Depends(require(Permission.RUNNER_RETIRE))],
)
def retire_host(host_id: str, request: HostRetireRequest, services: Services) -> HostRetireResponse:
    """Retire a host — its token is revoked and every runner under it retired; 409 naming each runner's held chunks
    unless ``force``, which releases them. Re-running on a retired host re-runs each runner's release pass."""
    host = _host(services, host_id)
    under = services.registry.list_runners(include_retired=True, host_id=host_id)
    held = [route for r in under for route in services.chunks.route.live_routes_of_runner(r.runner_id)]
    changes = chunk_events.ChunkChanged.before_many(services, [r.chunk_id for r in held])
    try:
        outcome = services.host_retirement.retire(host, by=request.by, force=request.force)
    except HostHoldsRoutes as exc:
        raise _conflict(exc) from exc
    except HostRetirementInterrupted as exc:
        _publish_host_retirement(services, host, under, exc.committed, by=request.by, changes=changes)
        raise _conflict(exc) from exc
    _publish_host_retirement(services, host, under, outcome, by=request.by, changes=changes)
    released = [r.chunk_id for result in outcome.runners.values() for r in result.released]
    return HostRetireResponse(host=_reread(services, host_id), released_chunk_ids=released)
