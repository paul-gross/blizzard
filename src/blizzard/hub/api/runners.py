"""Runner routes — the **operator** half of the fleet registry: add, list, read, pause,
resume, enroll, retire, reinstate, and token revocation. Every verb addresses a runner by its
hub-minted id and never resolves a name. The runner-authenticated half is :mod:`blizzard.hub.api.fleet`.

Controllers stay read-only over the store (``bzh:controller-read-only``)."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import Annotated, ClassVar

from dishka.integrations.fastapi import FromDishka, inject_sync
from fastapi import APIRouter, Depends, Query, status

from blizzard.auth_core import Permission
from blizzard.foundation.clock import IClock
from blizzard.foundation.declared_lever import DeclaredLever
from blizzard.foundation.hub_event_types import RunnerChangeKind
from blizzard.foundation.node_steps import Executor
from blizzard.foundation.store.utc import iso_utc
from blizzard.foundation.subscription_miss import SampleMissReason
from blizzard.hub.api import chunk_events, problems, problems_runners
from blizzard.hub.api import queue as queue_api
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.chunk_frames import ChunkFrameLoader
from blizzard.hub.api.problem_exceptions import ProblemHTTPException
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.route import IReadChunkRouteRepository
from blizzard.hub.domain.execution.fleet import (
    FleetService,
    ReinstateFailed,
    ReinstateInterrupted,
    ReleasedRoute,
    RetireInterrupted,
    RetireOutcome,
)
from blizzard.hub.domain.graph.model import IReadNodeExecutors
from blizzard.hub.domain.projects.model import IReadProjectRepository, Project, ProjectId
from blizzard.hub.domain.runners.bindings import BindingFlags, claimable_projects, flag_bindings, offered_projects
from blizzard.hub.domain.runners.enrollment import RunnerEnrollmentService
from blizzard.hub.domain.runners.gate_enforceability import flag_unenforceable_gates
from blizzard.hub.domain.runners.host import Host, HostRetired, IReadHostRegistry
from blizzard.hub.domain.runners.host_enrollment import HostEnrollmentService
from blizzard.hub.domain.runners.registration import (
    IReadRunnerRegistry,
    PerSubscriptionUsageView,
    RunnerBinding,
    RunnerHoldsRoutes,
    RunnerLifecycleConflict,
    RunnerLiveness,
    RunnerNotEnrolled,
    RunnerNotRetired,
    RunnerRegistration,
    RunnerRetired,
    ServedEntry,
)
from blizzard.hub.domain.runners.served import UnknownServedProject
from blizzard.hub.events.broker import EventBroker
from blizzard.wire.queue import NextClaimResponse
from blizzard.wire.runner import (
    ExternalSubscriptionUsageWindowView,
    RunnerAddRequest,
    RunnerAddResponse,
    RunnerBindingView,
    RunnerDecliningView,
    RunnerEnrollmentResponse,
    RunnerLifecycleRequest,
    RunnerPauseRequest,
    RunnerRegistryListResponse,
    RunnerRegistryView,
    RunnerRetireRequest,
    RunnerRetireResponse,
    RunnerTokenRevocationResponse,
    RunnerView,
    ServedProjectView,
    UnenforceableGateView,
)
from blizzard.wire.runner import (
    RunnerCapability as RunnerCapabilityWire,
)
from blizzard.wire.runner import (
    SubscriptionUsageView as SubscriptionUsageViewWire,
)
from blizzard.wire.vocabulary import open_value

router = APIRouter(
    prefix="/api", tags=["runners"], dependencies=[Depends(reject_runner_principal), Depends(tenant_bound)]
)

Fleet = FromDishka[FleetService]
Runners = FromDishka[IReadRunnerRegistry]
Hosts = FromDishka[IReadHostRegistry]
Projects = FromDishka[IReadProjectRepository]
Executors = FromDishka[IReadNodeExecutors]
Routes = FromDishka[IReadChunkRouteRepository]
Events = FromDishka[EventBroker]
Frames = FromDishka[ChunkFrameLoader]
Clock = FromDishka[IClock]
Enrollment = FromDishka[RunnerEnrollmentService]
HostEnrollment = FromDishka[HostEnrollmentService]


@dataclass(frozen=True)
class RunnerReads:
    """What one runner's registry view is read through: its registry, its liveness, and the projects, hosts and node
    executors the view is resolved against."""

    registry: IReadRunnerRegistry
    fleet: FleetService
    clock: IClock
    projects: IReadProjectRepository
    hosts: IReadHostRegistry
    executors: IReadNodeExecutors

    def registration(self, runner_id: str) -> RunnerRegistration:
        """The runner's loaded registration, 404 on an unknown one."""
        registration = self.registry.get_runner(runner_id)
        if registration is None:
            raise ProblemHTTPException(partial(problems.runner_not_found_problem, runner_id))
        return registration

    def detail(self, registration: RunnerRegistration) -> RunnerRegistryView:
        """One runner's registry view, its served projects read alongside."""
        return registry_view(
            self.fleet.get_liveness(registration),
            now=self.clock.now(),
            projects=served_projects(self.projects, registration.served),
            hosts=hosts_of(self.hosts, [registration]),
            executors=executors_for(self.executors, [registration]),
        )

    def reread(self, runner_id: str) -> RunnerRegistryView:
        """The runner read back after a write, so the response reports what the write set."""
        return self.detail(self.registration(runner_id))


@dataclass(frozen=True)
class RunnerBrake:
    """One operator write of a runner's fleet pause brake. Which way the brake moves is
    the subclass's, and is the only thing that differs between the two verbs."""

    reads: RunnerReads
    fleet: FleetService
    events: EventBroker
    runner_id: str
    by: str

    paused: ClassVar[bool]
    kind: ClassVar[RunnerChangeKind]

    def set(self) -> RunnerRegistryView:
        """Write the fact, publish the frame, and read the runner back; 404 on an unknown one."""
        registration = self.reads.registration(self.runner_id)
        fact_id = self.fleet.set_paused(registration, paused=self.paused, by=self.by)
        self.events.publish_runner_changed(
            self.runner_id,
            kind=self.kind,
            runner_name=registration.name,
            by=self.by,
            key=f"runner_pause_facts:{fact_id}",
        )
        # Re-resolved after the write (not the pre-write `registration`) so the response
        # reports the brake this call just set, not its pre-write value.
        return self.reads.reread(self.runner_id)


class Paused(RunnerBrake):
    paused = True
    kind = "paused"


class Resumed(RunnerBrake):
    paused = False
    kind = "resumed"


def runner_view(
    liveness: RunnerLiveness, *, now: datetime, projects: Mapping[ProjectId, Project], hosts: Mapping[str, Host]
) -> RunnerView:
    """The runner's own view of its registration — the fleet read, which only a runner that
    has registered reaches. ``hosts`` holds the host it stands under (:func:`hosts_of`), which its bindings are
    flagged against."""
    r = liveness.registration
    flags = flags_of(r, hosts)
    # A never-connected runner has no workspace, registration, or contact to render; the fleet
    # read refuses one before it gets here.
    assert r.workspace_id is not None and r.registered_at is not None and r.last_seen_at is not None
    return RunnerView(
        runner_id=r.runner_id,
        runner_name=r.name,
        workspace_id=r.workspace_id,
        registered_at=iso_utc(r.registered_at),
        last_seen_at=iso_utc(r.last_seen_at),
        online=liveness.online,
        hub_paused=r.hub_paused,
        locally_paused=r.locally_paused,
        locally_paused_by=r.locally_paused_by,
        locally_paused_reason=r.locally_paused_reason,
        env_capacity=r.env_capacity,
        agents=r.agents,
        subscriptions=_subscription_views(r, now=now),
        capabilities=_capability_views(r),
        retired=r.retired,
        retired_at=_iso_or_none(r.retired_at),
        retired_by=r.retired_by,
        gates=list(r.gates),
        projects=served_views(r.served, projects),
        bindings=binding_views(r.bindings, flags, r.agents),
        subscription=r.subscription,
        subscription_unrostered=flags.subscription_unrostered,
        declining=declining_views(r),
    )


def executors_for(
    executors: IReadNodeExecutors, registrations: Iterable[RunnerRegistration]
) -> Mapping[str, Collection[Executor]]:
    """Where each node name runs across the tenant's graphs — read only when some registration declares a gate, since
    nothing else consults it."""
    if not any(r.gates for r in registrations):
        return {}
    return executors.executors_by_node_name()


def unenforceable_views(
    gates: Collection[str], executors: Mapping[str, Collection[Executor]]
) -> list[UnenforceableGateView]:
    """The wire flags for those of ``gates`` no graph runs on a runner (:func:`flag_unenforceable_gates`)."""
    return [UnenforceableGateView(node=g.node, reason=g.reason) for g in flag_unenforceable_gates(gates, executors)]


def registry_view(
    liveness: RunnerLiveness,
    *,
    now: datetime,
    projects: Mapping[ProjectId, Project],
    hosts: Mapping[str, Host],
    executors: Mapping[str, Collection[Executor]],
) -> RunnerRegistryView:
    """One runner as the operator registry shows it — a never-connected runner included, with
    no workspace, registration, contact or capabilities until it first registers. ``projects``
    holds the projects its served declaration resolved to (:func:`served_projects`), and ``hosts`` the hosts the
    runners stand under (:func:`hosts_of`), which their bindings are flagged against. ``executors`` is where each node
    name runs across the tenant's graphs, which the runner's gates are flagged against."""
    r = liveness.registration
    flags = flags_of(r, hosts)
    return RunnerRegistryView(
        runner_id=r.runner_id,
        runner_name=r.name,
        connection=liveness.connection(),
        online=liveness.online,
        added_at=iso_utc(r.added_at),
        added_by=r.added_by,
        workspace_id=r.workspace_id,
        registered_at=_iso_or_none(r.registered_at),
        last_seen_at=_iso_or_none(r.last_seen_at),
        hub_paused=r.hub_paused,
        locally_paused=r.locally_paused,
        locally_paused_by=r.locally_paused_by,
        locally_paused_reason=r.locally_paused_reason,
        env_capacity=r.env_capacity,
        agents=r.agents,
        subscriptions=_subscription_views(r, now=now),
        capabilities=_capability_views(r),
        retired=r.retired,
        retired_at=_iso_or_none(r.retired_at),
        retired_by=r.retired_by,
        gates=list(r.gates),
        unenforceable_gates=unenforceable_views(r.gates, executors),
        projects=served_views(r.served, projects, claimable_projects(r, host_of(r, hosts))),
        host_id=r.host_id,
        bindings=binding_views(r.bindings, flags, r.agents),
        subscription=r.subscription,
        subscription_unrostered=flags.subscription_unrostered,
        declining=declining_views(r),
        version=r.reported_version,
        version_standing=r.version_standing(),
        version_refusal=r.version_refusal,
    )


def hosts_of(hosts: IReadHostRegistry, registrations: Sequence[RunnerRegistration]) -> Mapping[str, Host]:
    """The hosts the registrations stand under, by id — one subset read for the hosts named,
    none for runners that stand alone."""
    ids = {r.host_id for r in registrations if r.host_id is not None}
    if not ids:
        return {}
    return hosts.get_hosts(sorted(ids))


def host_of(registration: RunnerRegistration, hosts: Mapping[str, Host]) -> Host | None:
    """The host the runner stands under, ``None`` for a runner that stands alone."""
    return hosts.get(registration.host_id) if registration.host_id is not None else None


def flags_of(registration: RunnerRegistration, hosts: Mapping[str, Host]) -> BindingFlags:
    """The flags the runner's host raises against its declaration — none for a runner that stands alone."""
    return flag_bindings(registration.bindings, registration.subscription, host_of(registration, hosts))


def binding_views(
    bindings: Iterable[RunnerBinding] | None, flags: BindingFlags, agents: int | None
) -> list[RunnerBindingView] | None:
    """Each declared binding with its workspace flag; ``None`` when the runner declared none. The deprecated
    ``agents`` of each is the runner's shared count, ``0`` where it sent none."""
    if bindings is None:
        return None
    return [
        RunnerBindingView(
            project=b.project,
            workspace_id=b.workspace_id,
            agents=agents or 0,
            workspace_unreported=flags.workspace_unreported(b),
        )
        for b in bindings
    ]


def served_projects(projects: IReadProjectRepository, served: Iterable[ServedEntry]) -> Mapping[ProjectId, Project]:
    """The projects the served entries resolved to, in one read."""
    ids = sorted({ProjectId(e.project_id) for e in served if e.project_id is not None})
    return projects.get_many(ids) if ids else {}


def served_views(
    served: Iterable[ServedEntry],
    projects: Mapping[ProjectId, Project],
    claimable: AbstractSet[str] = frozenset(),
) -> list[ServedProjectView]:
    """Each served entry as declared, with the project it resolved to named by its slug and name as they stand, and
    flagged claimable where its project is among ``claimable``."""
    views: list[ServedProjectView] = []
    for entry in served:
        project = projects.get(ProjectId(entry.project_id)) if entry.project_id is not None else None
        views.append(
            ServedProjectView(
                entry=entry.entry,
                project_id=entry.project_id,
                slug=None if project is None else project.slug,
                name=None if project is None else project.name,
                color=None if project is None else str(project.color),
                claimable=entry.project_id in claimable,
            )
        )
    return views


def _iso_or_none(value: datetime | None) -> str | None:
    return iso_utc(value) if value is not None else None


def _subscription_views(r: RunnerRegistration, *, now: datetime) -> list[SubscriptionUsageViewWire]:
    return usage_views(PerSubscriptionUsageView.every(r, now=now))


def usage_views(views: Iterable[PerSubscriptionUsageView]) -> list[SubscriptionUsageViewWire]:
    """The wire shape of each per-subscription usage view, in order — a runner's and a host's alike."""
    return [
        SubscriptionUsageViewWire(
            slug=view.slug,
            name=view.name,
            sampled_at=_iso_or_none(view.sampled_at),
            windows=[
                ExternalSubscriptionUsageWindowView(
                    window=w.window,
                    utilization_pct=w.utilization_pct,
                    resets_at=iso_utc(w.resets_at),
                    window_seconds=w.window_seconds,
                )
                for w in view.windows
            ],
            condition=view.condition,
            miss_reason=SampleMissReason.recognized(view.miss_reason),
            missed_at=_iso_or_none(view.missed_at),
        )
        for view in views
    ]


def declining_views(r: RunnerRegistration) -> list[RunnerDecliningView]:
    return [
        RunnerDecliningView(
            lever=d.lever,
            lever_kind=open_value(DeclaredLever, d.lever),
            detail=d.detail,
            subscription=d.subscription,
            until=_iso_or_none(d.until),
        )
        for d in r.declining
    ]


def _capability_views(r: RunnerRegistration) -> list[RunnerCapabilityWire]:
    return [
        RunnerCapabilityWire(
            harness_id=c.harness_id,
            version=c.version,
            tiers=list(c.tiers),
            supported_models=list(c.supported_models),
            default=c.default,
            available=c.available,
        )
        for c in r.capabilities
    ]


def _registration(registry: IReadRunnerRegistry, runner_id: str) -> RunnerRegistration:
    """The runner's loaded registration, 404 on an unknown one."""
    registration = registry.get_runner(runner_id)
    if registration is None:
        raise ProblemHTTPException(partial(problems.runner_not_found_problem, runner_id))
    return registration


@router.post(
    "/runners",
    response_model=RunnerAddResponse,
    status_code=status.HTTP_201_CREATED,
)
@inject_sync
def add_runner(
    request: RunnerAddRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.RUNNER_ADD))],
    enrollment: Enrollment,
    host_enrollment: HostEnrollment,
    hosts: Hosts,
    events: Events,
) -> RunnerAddResponse:
    """Add a runner under the initial ``name`` — the hub mints its id and bearer token together,
    and the plaintext token is returned once. The runner is never connected until it first
    registers with that token. Names are not unique: adding a held name adds another runner.
    ``projects`` is what it serves until a registration declares otherwise; an entry that names no
    project of the tenant is refused 422, and nothing is added."""
    try:
        if request.host_id is None:
            added = enrollment.add(request.name, by=identity.username, projects=request.projects)
        else:
            host = hosts.get_host(request.host_id)
            if host is None:
                raise ProblemHTTPException(partial(problems_runners.host_not_found_problem, request.host_id))
            added = host_enrollment.add_runner(host, request.name, by=identity.username, projects=request.projects)
    except UnknownServedProject as exc:
        raise ProblemHTTPException(partial(problems.unknown_served_project_problem, exc)) from exc
    except HostRetired as exc:
        raise ProblemHTTPException(partial(problems.host_retired_problem, exc, status_code=409)) from exc
    events.publish_runner_changed(added.runner_id, kind="added", runner_name=added.name, by=identity.username)
    return RunnerAddResponse(runner_id=added.runner_id, runner_name=added.name, token=added.token)


@router.post(
    "/runners/{runner_id}/enrollments",
    response_model=RunnerEnrollmentResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(Permission.RUNNER_ADD))],
)
@inject_sync
def enroll_runner(runner_id: str, registry: Runners, enrollment: Enrollment) -> RunnerEnrollmentResponse:
    """Rotate ``runner_id``'s bearer token — the plaintext is returned once, and the token it
    replaces stops resolving, the one ``add`` minted included.

    Requires an added runner (404 otherwise), connected or not. A retired runner is refused
    409: ``reinstate`` is the one reinstatement lever. So is a runner retired or reinstated while the enroll was
    deciding: 409, with nothing minted or revoked."""
    registration = _registration(registry, runner_id)
    try:
        token = enrollment.enroll(registration)
    except RunnerRetired as exc:
        raise ProblemHTTPException(partial(problems.runner_retired_problem, exc, status_code=409)) from exc
    except RunnerLifecycleConflict as exc:
        raise ProblemHTTPException(partial(problems.runner_lifecycle_conflict_problem, exc)) from exc
    return RunnerEnrollmentResponse(runner_id=runner_id, token=token)


@router.get(
    "/runners", response_model=RunnerRegistryListResponse, dependencies=[Depends(require(Permission.FLEET_VIEW))]
)
@inject_sync
def list_runners(
    fleet: Fleet,
    clock: Clock,
    projects: Projects,
    hosts: Hosts,
    executors: Executors,
    include_retired: Annotated[bool, Query()] = False,
) -> RunnerRegistryListResponse:
    """The fleet registry — every added runner, oldest first, with its connection condition and
    paused state; a retired runner excluded by default, included and marked when ``include_retired``. Not paged: a
    tenant has tens of runners, and callers read the whole roster."""
    now = clock.now()
    listed = fleet.list_with_liveness(now=now, include_retired=include_retired)
    served = served_projects(projects, (e for item in listed for e in item.registration.served))
    standing = hosts_of(hosts, [item.registration for item in listed])
    running = executors_for(executors, [item.registration for item in listed])
    return RunnerRegistryListResponse(
        runners=[registry_view(item, now=now, projects=served, hosts=standing, executors=running) for item in listed]
    )


@router.get(
    "/runners/{runner_id}", response_model=RunnerRegistryView, dependencies=[Depends(require(Permission.FLEET_VIEW))]
)
@inject_sync
def get_runner(
    runner_id: str,
    reads: FromDishka[RunnerReads],
) -> RunnerRegistryView:
    """One runner's connection condition and paused state — the operator's detail read,
    symmetric with the list. 404 on unknown."""
    return reads.reread(runner_id)


@router.get(
    "/runners/{runner_id}/next-claim",
    response_model=NextClaimResponse,
    dependencies=[Depends(require(Permission.FLEET_VIEW))],
)
@inject_sync
def get_runner_next_claim(
    runner_id: str,
    registry: Runners,
    hosts: Hosts,
    queues: FromDishka[queue_api.QueueViewAssembler],
    facts: FromDishka[IReadChunkFactsRepository],
) -> NextClaimResponse:
    """The hub's best account of the chunk one runner reaches for next and every chunk it passes on the way — walked
    from the runner's served projects and bindings and its latest capability snapshot. A retired or paused runner is
    answered like any other; 404 on unknown."""
    registration = _registration(registry, runner_id)
    host = hosts.get_host(registration.host_id) if registration.host_id is not None else None
    return queues.next_claim(
        facts.load_live_statuses(),
        served=offered_projects(registration, host),
        capabilities=registration.capabilities,
    ).view


@router.post(
    "/runners/{runner_id}/pause",
    response_model=RunnerRegistryView,
)
@inject_sync
def pause_runner(
    runner_id: str,
    request: RunnerPauseRequest,
    reads: FromDishka[RunnerReads],
    fleet: Fleet,
    events: Events,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.RUNNER_PAUSE))],
) -> RunnerRegistryView:
    """Set a runner's pause brake — no new claims; in-flight chunks run on."""
    return Paused(reads, fleet, events, runner_id, identity.username).set()


@router.post(
    "/runners/{runner_id}/resume",
    response_model=RunnerRegistryView,
)
@inject_sync
def resume_runner(
    runner_id: str,
    request: RunnerPauseRequest,
    reads: FromDishka[RunnerReads],
    fleet: Fleet,
    events: Events,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.RUNNER_PAUSE))],
) -> RunnerRegistryView:
    """Clear a runner's pause brake — it resumes claiming on its next pull."""
    return Resumed(reads, fleet, events, runner_id, identity.username).set()


@router.post(
    "/runners/{runner_id}/retire",
    response_model=RunnerRetireResponse,
)
@inject_sync
def retire_runner(
    runner_id: str,
    request: RunnerRetireRequest,
    reads: FromDishka[RunnerReads],
    fleet: Fleet,
    routes: Routes,
    events: Events,
    frames: Frames,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.RUNNER_RETIRE))],
) -> RunnerRetireResponse:
    """Retire a runner — its token is revoked and its claims and registrations refused;
    409 naming each held chunk unless ``force``, which releases them. Re-running on a
    retired runner re-runs the release pass. A retire — a re-run included — whose runner
    was retired or reinstated after it was read is refused 409, revoking no token and
    releasing no route the runner holds since; what it committed first is still published.
    Run it again."""
    registration = reads.registration(runner_id)
    held = routes.live_routes_of_runner(runner_id)
    changes = chunk_events.ChunkChanged.before_many(frames, [r.chunk_id for r in held])
    try:
        outcome = fleet.retire(registration, by=identity.username, force=request.force)
    except RetireInterrupted as exc:
        publish_retirement(events, frames, registration, exc.committed, by=identity.username, changes=changes)
        raise ProblemHTTPException(partial(problems.runner_retire_interrupted_problem, exc)) from exc
    except RunnerHoldsRoutes as exc:
        raise ProblemHTTPException(partial(problems.runner_holds_routes_problem, exc)) from exc
    except RunnerLifecycleConflict as exc:
        raise ProblemHTTPException(partial(problems.runner_lifecycle_conflict_problem, exc)) from exc
    publish_retirement(events, frames, registration, outcome, by=identity.username, changes=changes)
    return RunnerRetireResponse(
        runner=reads.reread(runner_id), released_chunk_ids=[r.chunk_id for r in outcome.released]
    )


def publish_retirement(
    events: EventBroker,
    frames: ChunkFrameLoader,
    registration: RunnerRegistration,
    outcome: RetireOutcome,
    *,
    by: str,
    changes: Mapping[str, chunk_events.ChunkChanged],
) -> None:
    """Publish what one runner retire wrote: a ``detached`` frame per released chunk (``changes`` holds each one read
    before the retire), the queue's change when any was released, and the runner's ``retired`` frame unless the retire
    was a re-run."""
    _publish_releases(events, frames, outcome.released, changes)
    if outcome.fact_id is not None:
        events.publish_runner_changed(
            registration.runner_id,
            kind="retired",
            runner_name=registration.name,
            by=by,
            key=f"runner_lifecycle_facts:{outcome.fact_id}",
        )


def _publish_releases(
    events: EventBroker,
    frames: ChunkFrameLoader,
    released: Sequence[ReleasedRoute],
    changes: Mapping[str, chunk_events.ChunkChanged],
) -> None:
    """A ``detached`` frame per released chunk, each from its state read before the write when ``changes`` holds
    it, and the queue's change when any was released."""
    for route in released:
        change = changes.get(route.chunk_id) or chunk_events.ChunkChanged.before(frames, route.chunk_id)
        change.publish(cause="detached", key=f"route_released:{route.released_id}")
    if released:
        events.publish_queue_changed()  # released chunks re-enter the ready queue


@router.post(
    "/runners/{runner_id}/reinstate",
    response_model=RunnerRegistryView,
)
@inject_sync
def reinstate_runner(
    runner_id: str,
    request: RunnerLifecycleRequest,
    reads: FromDishka[RunnerReads],
    fleet: Fleet,
    routes: Routes,
    events: Events,
    frames: Frames,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.RUNNER_RETIRE))],
) -> RunnerRegistryView:
    """Reinstate a retired runner — any token it still carries is revoked with the reversal, and it stays unenrolled
    until enrolled afresh; 409 when not retired, when it stands under a retired host, and when its lifecycle moved
    while the reinstate was decided. A route its retirement left held is released first, as the retire's own release
    pass would, and its ``detached`` frame published."""
    registration = reads.registration(runner_id)
    held = routes.live_routes_of_runner(runner_id)
    changes = chunk_events.ChunkChanged.before_many(frames, [r.chunk_id for r in held])
    try:
        outcome = fleet.reinstate(registration, by=identity.username)
    except ReinstateInterrupted as exc:
        _publish_releases(events, frames, exc.released, changes)
        raise ProblemHTTPException(partial(problems_runners.runner_reinstate_interrupted_problem, exc)) from exc
    except ReinstateFailed as exc:
        _publish_releases(events, frames, exc.released, changes)
        raise exc.cause from exc
    except RunnerNotRetired as exc:
        raise ProblemHTTPException(partial(problems_runners.runner_not_retired_problem, exc)) from exc
    except HostRetired as exc:
        raise ProblemHTTPException(partial(problems.host_retired_problem, exc, status_code=409)) from exc
    except RunnerLifecycleConflict as exc:
        raise ProblemHTTPException(partial(problems.runner_lifecycle_conflict_problem, exc)) from exc
    _publish_releases(events, frames, outcome.released, changes)
    events.publish_runner_changed(
        runner_id,
        kind="reinstated",
        runner_name=registration.name,
        by=identity.username,
        key=f"runner_lifecycle_facts:{outcome.fact_id}",
    )
    return reads.reread(runner_id)


@router.post(
    "/runners/{runner_id}/token-revocations",
    response_model=RunnerTokenRevocationResponse,
    status_code=status.HTTP_201_CREATED,
)
@inject_sync
def revoke_runner_token(
    runner_id: str,
    request: RunnerLifecycleRequest,
    reads: FromDishka[RunnerReads],
    fleet: Fleet,
    events: Events,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.RUNNER_RETIRE))],
) -> RunnerTokenRevocationResponse:
    """Revoke a runner's token — it stays added and must be re-enrolled; 409 when unenrolled, and when it was retired
    or reinstated while the revocation was decided."""
    registration = reads.registration(runner_id)
    try:
        revocation_id = fleet.revoke_token(registration, by=identity.username)
    except RunnerNotEnrolled as exc:
        raise ProblemHTTPException(partial(problems_runners.runner_not_enrolled_problem, exc)) from exc
    except RunnerLifecycleConflict as exc:
        raise ProblemHTTPException(partial(problems.runner_lifecycle_conflict_problem, exc)) from exc
    events.publish_runner_changed(
        runner_id,
        kind="token-revoked",
        runner_name=registration.name,
        by=identity.username,
        key=f"runner_token_revocations:{revocation_id}",
    )
    return RunnerTokenRevocationResponse(runner=reads.reread(runner_id))
