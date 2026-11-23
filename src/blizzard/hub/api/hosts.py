"""Host routes — the **operator** half of the host registry: add, list, read, adopt, release, enroll, revoke a token,
and retire. Every verb addresses a host by its hub-minted id and never resolves a name. The host-authenticated half is
:mod:`blizzard.hub.api.fleet_host`.

Controllers stay read-only over the store (``bzh:controller-read-only``); a host's bearer token is no session, so the
permission gate refuses it."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject_sync
from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse

from blizzard.auth_core import Permission
from blizzard.foundation.clock import IClock
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api import chunk_events, problems, problems_runners
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.chunk_frames import ChunkFrameLoader
from blizzard.hub.api.problem_exceptions import ProblemHTTPException
from blizzard.hub.api.problem_rendering import problem_response
from blizzard.hub.api.problems import (
    host_name_taken_problem,
    runner_already_hosted_problem,
    runner_lifecycle_conflict_problem,
    runner_not_found_problem,
    runner_retired_problem,
)
from blizzard.hub.api.runners import (
    Clock,
    Events,
    Frames,
    HostEnrollment,
    Hosts,
    Routes,
    Runners,
    publish_retirement,
    usage_views,
)
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.domain.execution.host_retirement import (
    HostRetirementInterrupted,
    HostRetirementService,
    HostRetireOutcome,
)
from blizzard.hub.domain.runners.host import (
    Host,
    HostHoldsRoutes,
    HostLifecycleConflict,
    HostNameTaken,
    HostNotEnrolled,
    HostRetired,
    HostRetirementUnfinished,
    HostUsage,
    IReadHostRegistry,
    RunnerAlreadyHosted,
    RunnerNotUnderHost,
    UnknownRunner,
)
from blizzard.hub.domain.runners.registration import (
    IReadRunnerRegistry,
    PerSubscriptionUsageView,
    RunnerLifecycleConflict,
    RunnerRegistration,
    RunnerRetired,
)
from blizzard.hub.events.broker import EventBroker
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

Retirement = FromDishka[HostRetirementService]


@dataclass(frozen=True)
class HostReads:
    """What one host's registry view is read through: the host and runner registries and the clock."""

    hosts: IReadHostRegistry
    registry: IReadRunnerRegistry
    clock: IClock

    def host(self, host_id: str) -> Host:
        """The host's loaded record, 404 on an unknown one."""
        host = self.hosts.get_host(host_id)
        if host is None:
            raise ProblemHTTPException(partial(problems_runners.host_not_found_problem, host_id))
        return host

    def reread(self, host_id: str) -> HostRegistryView:
        """The host read back after a write, so the response reports what the write set."""
        return registry_view(self, self.host(host_id))


def _iso_or_none(value: datetime | None) -> str | None:
    return iso_utc(value) if value is not None else None


def _subscription_usage(
    host: Host, usage: HostUsage, under: Sequence[RunnerRegistration], *, now: datetime
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


def registry_view(
    reads: HostReads,
    host: Host,
    usage: HostUsage | None = None,
    under: Sequence[RunnerRegistration] | None = None,
) -> HostRegistryView:
    """One host as the operator registry shows it, the runners standing under it read alongside. ``usage`` and
    ``under`` are the host's reported usage and its runners (retired included) when the caller read them in bulk
    across several hosts, else they are read here; a supplied ``under``, even empty, is used as given."""
    now = reads.clock.now()
    if under is None:
        under = reads.registry.list_runners(include_retired=True, host_id=host.host_id)
    if usage is None:
        usage = reads.hosts.usage_of([host.host_id])[host.host_id]
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
        urls=list(host.urls),
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


def _conflict(exc: Exception) -> ProblemHTTPException:
    """The 409 problem the domain's lifecycle refusal names."""
    match exc:
        case RunnerAlreadyHosted():
            return ProblemHTTPException(partial(problems.runner_already_hosted_problem, exc))
        case RunnerRetired():
            return ProblemHTTPException(partial(problems.runner_retired_problem, exc, status_code=409))
        case HostRetired():
            return ProblemHTTPException(partial(problems.host_retired_problem, exc, status_code=409))
        case RunnerLifecycleConflict():
            return ProblemHTTPException(partial(problems.runner_lifecycle_conflict_problem, exc))
        case HostRetirementUnfinished():
            return ProblemHTTPException(partial(problems_runners.host_retirement_unfinished_problem, exc))
        case HostLifecycleConflict():
            return ProblemHTTPException(partial(problems_runners.host_lifecycle_conflict_problem, exc))
        case HostNotEnrolled():
            return ProblemHTTPException(partial(problems_runners.host_not_enrolled_problem, exc))
        case HostHoldsRoutes():
            return ProblemHTTPException(partial(problems_runners.host_holds_routes_problem, exc))
        case HostRetirementInterrupted():
            return ProblemHTTPException(partial(problems_runners.host_retirement_interrupted_problem, exc))
        case _:
            raise exc


def _publish_host_retirement(
    registry: IReadRunnerRegistry,
    events: EventBroker,
    frames: ChunkFrameLoader,
    host: Host,
    under: list[RunnerRegistration],
    outcome: HostRetireOutcome,
    *,
    by: str,
    changes: dict[str, chunk_events.ChunkChanged],
) -> None:
    """The frames for what a host retire committed — each runner it retired, and the host's own fact. A runner the
    retire found under the host that ``under``, read before it, did not hold is read here."""
    registrations = {registration.runner_id: registration for registration in under}
    registrations.update(registry.get_runners(outcome.runners.keys() - registrations.keys()))
    for runner_id, retired in outcome.runners.items():
        publish_retirement(events, frames, registrations[runner_id], retired, by=by, changes=changes)
    if outcome.fact_id is not None:
        events.publish_host_changed(
            host.host_id, kind="retired", host_name=host.name, by=by, key=f"host_lifecycle_facts:{outcome.fact_id}"
        )


def _unknown_runner(exc: UnknownRunner | RunnerNotUnderHost) -> ProblemHTTPException:
    return ProblemHTTPException(partial(problems.runner_not_found_problem, exc.runner_id))


@router.post("/hosts", response_model=HostAddResponse, status_code=status.HTTP_201_CREATED)
@inject_sync
def add_host(
    request: HostAddRequest,
    http_request: Request,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.RUNNER_RETIRE))],
    host_enrollment: HostEnrollment,
    events: Events,
) -> HostAddResponse | JSONResponse:
    """Add a host under ``name`` — the hub mints its id and bearer token together, and the plaintext token is returned
    once. ``adopt`` places existing runners under it in the same write: a runner of another tenant is refused as
    unknown (404), one already under a host or a retired one 409, and so is one retired or reinstated while the add
    was decided; nothing is added. A name a live host holds is refused 409."""
    try:
        added = host_enrollment.add(request.name, by=identity.username, adopt=request.adopt)
    except HostNameTaken as exc:
        return problem_response(host_name_taken_problem(exc, instance=http_request.url.path))
    except UnknownRunner as exc:
        return problem_response(runner_not_found_problem(exc.runner_id, instance=http_request.url.path))
    except RunnerAlreadyHosted as exc:
        return problem_response(runner_already_hosted_problem(exc, instance=http_request.url.path))
    except RunnerRetired as exc:
        return problem_response(runner_retired_problem(exc, instance=http_request.url.path, status_code=409))
    except RunnerLifecycleConflict as exc:
        return problem_response(runner_lifecycle_conflict_problem(exc, instance=http_request.url.path))
    events.publish_host_changed(added.host_id, kind="added", host_name=added.name, by=identity.username)
    return HostAddResponse(host_id=added.host_id, host_name=added.name, token=added.token, adopted=list(added.adopted))


@router.get("/hosts", response_model=HostRegistryListResponse, dependencies=[Depends(require(Permission.FLEET_VIEW))])
@inject_sync
def list_hosts(
    hosts: Hosts, registry: Runners, clock: Clock, include_retired: Annotated[bool, Query()] = False
) -> HostRegistryListResponse:
    """The host registry — every added host, oldest first; a retired host excluded by default, included and marked when
    ``include_retired``. Not paged: a tenant has a handful of hosts, and callers read the whole roster."""
    reads = HostReads(hosts, registry, clock)
    listed = hosts.list_hosts(include_retired=include_retired)
    usage = hosts.usage_of([h.host_id for h in listed])
    under = registry.runners_under([h.host_id for h in listed])
    return HostRegistryListResponse(
        hosts=[registry_view(reads, h, usage[h.host_id], under.get(h.host_id, [])) for h in listed]
    )


@router.get("/hosts/{host_id}", response_model=HostRegistryView, dependencies=[Depends(require(Permission.FLEET_VIEW))])
@inject_sync
def get_host(host_id: str, hosts: Hosts, registry: Runners, clock: Clock) -> HostRegistryView:
    """One host's connection condition and the runners under it — the operator's detail read. 404 on unknown."""
    return HostReads(hosts, registry, clock).reread(host_id)


@router.post(
    "/hosts/{host_id}/adoptions",
    response_model=HostRegistryView,
    dependencies=[Depends(require(Permission.RUNNER_RETIRE))],
)
@inject_sync
def adopt_runners(
    host_id: str,
    request: HostRunnersRequest,
    hosts: Hosts,
    registry: Runners,
    clock: Clock,
    host_enrollment: HostEnrollment,
    events: Events,
) -> HostRegistryView:
    """Place existing runners under the host, all or none. A runner of another tenant is refused as unknown (404), one
    already under a host or a retired one 409, and so is a retired host — one retired while the adoption was decided
    included — and a runner retired or reinstated meanwhile."""
    reads = HostReads(hosts, registry, clock)
    host = reads.host(host_id)
    try:
        host_enrollment.adopt(host, request.runner_ids, by=request.by)
    except UnknownRunner as exc:
        raise _unknown_runner(exc) from exc
    except (RunnerAlreadyHosted, RunnerRetired, HostRetired, RunnerLifecycleConflict) as exc:
        raise _conflict(exc) from exc
    events.publish_host_changed(host_id, kind="adopted", host_name=host.name, by=request.by)
    return reads.reread(host_id)


@router.post(
    "/hosts/{host_id}/releases",
    response_model=HostRegistryView,
    dependencies=[Depends(require(Permission.RUNNER_RETIRE))],
)
@inject_sync
def release_runners(
    host_id: str,
    request: HostRunnersRequest,
    hosts: Hosts,
    registry: Runners,
    clock: Clock,
    host_enrollment: HostEnrollment,
    events: Events,
) -> HostRegistryView:
    """Return runners under the host to standing alone, keeping their ids and tokens, all or none. A runner not under
    the host is refused as unknown (404). A retired host releases only retired runners: an unretired one is refused 409
    until the host's retire is re-run. A host or runner whose lifecycle moved while the release was decided is refused
    409; run it again."""
    reads = HostReads(hosts, registry, clock)
    host = reads.host(host_id)
    try:
        host_enrollment.release(host, request.runner_ids, by=request.by)
    except (UnknownRunner, RunnerNotUnderHost) as exc:
        raise _unknown_runner(exc) from exc
    except (HostRetirementUnfinished, HostLifecycleConflict, RunnerLifecycleConflict) as exc:
        raise _conflict(exc) from exc
    events.publish_host_changed(host_id, kind="released", host_name=host.name, by=request.by)
    return reads.reread(host_id)


@router.post(
    "/hosts/{host_id}/enrollments",
    response_model=HostEnrollmentResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(Permission.RUNNER_RETIRE))],
)
@inject_sync
def enroll_host(
    host_id: str, hosts: Hosts, registry: Runners, clock: Clock, host_enrollment: HostEnrollment, events: Events
) -> HostEnrollmentResponse:
    """Rotate ``host_id``'s bearer token, or issue one after a revocation — the plaintext is returned once, and the
    token it replaces stops resolving. A retired host is refused 409."""
    host = HostReads(hosts, registry, clock).host(host_id)
    try:
        token = host_enrollment.enroll(host)
    except HostRetired as exc:
        raise _conflict(exc) from exc
    events.publish_host_changed(host_id, kind="token-enrolled", host_name=host.name)
    return HostEnrollmentResponse(host_id=host_id, token=token)


@router.post(
    "/hosts/{host_id}/token-revocations",
    response_model=HostTokenRevocationResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(Permission.RUNNER_RETIRE))],
)
@inject_sync
def revoke_host_token(
    host_id: str,
    request: HostLifecycleRequest,
    hosts: Hosts,
    registry: Runners,
    clock: Clock,
    host_enrollment: HostEnrollment,
    events: Events,
) -> HostTokenRevocationResponse:
    """Revoke a host's token and issue none — it stays added and must be enrolled afresh; 409 when unenrolled."""
    reads = HostReads(hosts, registry, clock)
    host = reads.host(host_id)
    try:
        revocation_id = host_enrollment.revoke_token(host, by=request.by)
    except HostNotEnrolled as exc:
        raise _conflict(exc) from exc
    events.publish_host_changed(
        host_id,
        kind="token-revoked",
        host_name=host.name,
        by=request.by,
        key=f"host_token_revocations:{revocation_id}",
    )
    return HostTokenRevocationResponse(host=reads.reread(host_id))


@router.post(
    "/hosts/{host_id}/retire",
    response_model=HostRetireResponse,
    dependencies=[Depends(require(Permission.RUNNER_RETIRE))],
)
@inject_sync
def retire_host(
    host_id: str,
    request: HostRetireRequest,
    hosts: Hosts,
    registry: Runners,
    clock: Clock,
    routes: Routes,
    retirement: Retirement,
    events: Events,
    frames: Frames,
) -> HostRetireResponse:
    """Retire a host — its token is revoked and every runner under it retired; 409 naming each runner's held chunks
    unless ``force``, which releases them. Re-running on a retired host re-runs each runner's release pass. A host
    retired by another retire while this one was decided is refused 409, and so is a retire reaching a runner retired
    or reinstated since the runners under the host were read, which leaves that runner's token and chunks alone; what
    the retire committed is still published. Run it again."""
    reads = HostReads(hosts, registry, clock)
    host = reads.host(host_id)
    under = registry.list_runners(include_retired=True, host_id=host_id)
    held = [route for r in under for route in routes.live_routes_of_runner(r.runner_id)]
    changes = chunk_events.ChunkChanged.before_many(frames, [r.chunk_id for r in held])
    try:
        outcome = retirement.retire(host, by=request.by, force=request.force)
    except (HostHoldsRoutes, HostLifecycleConflict) as exc:
        raise _conflict(exc) from exc
    except HostRetirementInterrupted as exc:
        _publish_host_retirement(registry, events, frames, host, under, exc.committed, by=request.by, changes=changes)
        raise _conflict(exc) from exc
    _publish_host_retirement(registry, events, frames, host, under, outcome, by=request.by, changes=changes)
    released = [r.chunk_id for result in outcome.runners.values() for r in result.released]
    return HostRetireResponse(host=reads.reread(host_id), released_chunk_ids=released)
