"""Runner routes — the **operator** half of the fleet registry: add, list, read, pause,
resume, enroll, retire, reinstate, and token revocation. Every verb addresses a runner by its
hub-minted id and never resolves a name. The runner-authenticated half is :mod:`blizzard.hub.api.fleet`.

Controllers stay read-only over the store (``bzh:controller-read-only``);
``reject_runner_principal`` confines a runner's bearer token to the fleet router."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, ClassVar

from fastapi import APIRouter, Depends, HTTPException, Query, status

from blizzard.auth_core import FLEET_VIEW, RUNNER_ADD, RUNNER_PAUSE, RUNNER_RETIRE
from blizzard.foundation.hub_event_types import RunnerChangeKind
from blizzard.foundation.store.utc import iso_utc
from blizzard.foundation.subscription_miss import SampleMissReason
from blizzard.hub.api import chunk_events
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.runners.registration import (
    PerSubscriptionUsageView,
    RunnerHoldsRoutes,
    RunnerLiveness,
    RunnerNotEnrolled,
    RunnerNotRetired,
    RunnerRegistration,
    RunnerRetired,
)
from blizzard.wire.runner import (
    ExternalSubscriptionUsageWindowView,
    RunnerAddRequest,
    RunnerAddResponse,
    RunnerEnrollmentResponse,
    RunnerLifecycleRequest,
    RunnerPauseRequest,
    RunnerRegistryListResponse,
    RunnerRegistryView,
    RunnerRetireRequest,
    RunnerRetireResponse,
    RunnerTokenRevocationResponse,
    RunnerView,
)
from blizzard.wire.runner import (
    RunnerCapability as RunnerCapabilityWire,
)
from blizzard.wire.runner import (
    SubscriptionUsageView as SubscriptionUsageViewWire,
)

router = APIRouter(prefix="/api", tags=["runners"], dependencies=[Depends(reject_runner_principal)])


@dataclass(frozen=True)
class RunnerBrake:
    """One operator write of a runner's fleet pause brake. Which way the brake moves is
    the subclass's, and is the only thing that differs between the two verbs."""

    services: HubServices
    runner_id: str
    by: str

    paused: ClassVar[bool]
    kind: ClassVar[RunnerChangeKind]

    def set(self) -> RunnerRegistryView:
        """Write the fact, publish the frame, and read the runner back; 404 on an unknown one."""
        registration = self.services.registry.get_runner(self.runner_id)
        if registration is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown runner {self.runner_id}")
        fact_id = self.services.fleet.set_paused(registration, paused=self.paused, by=self.by)
        self.services.events.publish_runner_changed(
            self.runner_id,
            kind=self.kind,
            runner_name=registration.name,
            by=self.by,
            key=f"runner_pause_facts:{fact_id}",
        )
        # Re-resolved after the write (not the pre-write `registration`) so the response
        # reports the brake this call just set, not its pre-write value.
        refreshed = self.services.registry.get_runner(self.runner_id)
        assert refreshed is not None  # just set_paused succeeded, so the runner exists
        return registry_view(self.services.fleet.get_liveness(refreshed), now=self.services.clock.now())


class Paused(RunnerBrake):
    paused = True
    kind = "paused"


class Resumed(RunnerBrake):
    paused = False
    kind = "resumed"


def runner_view(liveness: RunnerLiveness, *, now: datetime) -> RunnerView:
    """The runner's own view of its registration — the fleet read, which only a runner that
    has registered reaches."""
    r = liveness.registration
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
        subscriptions=_subscription_views(r, now=now),
        capabilities=_capability_views(r),
        retired=r.retired,
        retired_at=_iso_or_none(r.retired_at),
        retired_by=r.retired_by,
        gates=list(r.gates),
    )


def registry_view(liveness: RunnerLiveness, *, now: datetime) -> RunnerRegistryView:
    """One runner as the operator registry shows it — a never-connected runner included, with
    no workspace, registration, contact or capabilities until it first registers."""
    r = liveness.registration
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
        subscriptions=_subscription_views(r, now=now),
        capabilities=_capability_views(r),
        retired=r.retired,
        retired_at=_iso_or_none(r.retired_at),
        retired_by=r.retired_by,
        gates=list(r.gates),
    )


def _iso_or_none(value: datetime | None) -> str | None:
    return iso_utc(value) if value is not None else None


def _subscription_views(r: RunnerRegistration, *, now: datetime) -> list[SubscriptionUsageViewWire]:
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
        for view in PerSubscriptionUsageView.every(r, now=now)
    ]


def _capability_views(r: RunnerRegistration) -> list[RunnerCapabilityWire]:
    return [
        RunnerCapabilityWire(
            harness_id=c.harness_id,
            version=c.version,
            tiers=list(c.tiers),
            default=c.default,
            available=c.available,
        )
        for c in r.capabilities
    ]


def _registration(services: HubServices, runner_id: str) -> RunnerRegistration:
    """The runner's loaded registration, 404 on an unknown one."""
    registration = services.registry.get_runner(runner_id)
    if registration is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown runner {runner_id}")
    return registration


def _reread(services: HubServices, runner_id: str) -> RunnerRegistryView:
    """The runner read back after a write, so the response reports what the write set."""
    return registry_view(services.fleet.get_liveness(_registration(services, runner_id)), now=services.clock.now())


@router.post(
    "/runners",
    response_model=RunnerAddResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_runner(
    request: RunnerAddRequest,
    services: Annotated[HubServices, Depends(get_services)],
    identity: Annotated[ResolvedIdentity, Depends(require(RUNNER_ADD))],
) -> RunnerAddResponse:
    """Add a runner under the initial ``name`` — the hub mints its id and bearer token together,
    and the plaintext token is returned once. The runner is never connected until it first
    registers with that token. Names are not unique: adding a held name adds another runner."""
    added = services.enrollment.add(request.name, by=identity.username)
    services.events.publish_runner_changed(added.runner_id, kind="added", runner_name=added.name, by=identity.username)
    return RunnerAddResponse(runner_id=added.runner_id, runner_name=added.name, token=added.token)


@router.post(
    "/runners/{runner_id}/enrollments",
    response_model=RunnerEnrollmentResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(RUNNER_ADD))],
)
def enroll_runner(runner_id: str, services: Annotated[HubServices, Depends(get_services)]) -> RunnerEnrollmentResponse:
    """Rotate ``runner_id``'s bearer token — the plaintext is returned once, and the token it
    replaces stops resolving, the one ``add`` minted included.

    Requires an added runner (404 otherwise), connected or not. A retired runner is refused
    409: ``reinstate`` is the one reinstatement lever."""
    registration = _registration(services, runner_id)
    try:
        token = services.enrollment.enroll(registration)
    except RunnerRetired as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return RunnerEnrollmentResponse(runner_id=runner_id, token=token)


@router.get("/runners", response_model=RunnerRegistryListResponse, dependencies=[Depends(require(FLEET_VIEW))])
def list_runners(
    services: Annotated[HubServices, Depends(get_services)],
    include_retired: Annotated[bool, Query()] = False,
) -> RunnerRegistryListResponse:
    """The fleet registry — every added runner, oldest first, with its connection condition and
    paused state; a retired runner excluded by default, included and marked when ``include_retired``."""
    now = services.clock.now()
    return RunnerRegistryListResponse(
        runners=[
            registry_view(item, now=now) for item in services.fleet.list_with_liveness(include_retired=include_retired)
        ]
    )


@router.get("/runners/{runner_id}", response_model=RunnerRegistryView, dependencies=[Depends(require(FLEET_VIEW))])
def get_runner(runner_id: str, services: Annotated[HubServices, Depends(get_services)]) -> RunnerRegistryView:
    """One runner's connection condition and paused state — the operator's detail read,
    symmetric with the list. 404 on unknown."""
    return registry_view(services.fleet.get_liveness(_registration(services, runner_id)), now=services.clock.now())


@router.post(
    "/runners/{runner_id}/pause", response_model=RunnerRegistryView, dependencies=[Depends(require(RUNNER_PAUSE))]
)
def pause_runner(
    runner_id: str, request: RunnerPauseRequest, services: Annotated[HubServices, Depends(get_services)]
) -> RunnerRegistryView:
    """Set a runner's pause brake — no new claims; in-flight chunks run on."""
    return Paused(services, runner_id, request.by).set()


@router.post(
    "/runners/{runner_id}/resume", response_model=RunnerRegistryView, dependencies=[Depends(require(RUNNER_PAUSE))]
)
def resume_runner(
    runner_id: str, request: RunnerPauseRequest, services: Annotated[HubServices, Depends(get_services)]
) -> RunnerRegistryView:
    """Clear a runner's pause brake — it resumes claiming on its next pull."""
    return Resumed(services, runner_id, request.by).set()


@router.post(
    "/runners/{runner_id}/retire", response_model=RunnerRetireResponse, dependencies=[Depends(require(RUNNER_RETIRE))]
)
def retire_runner(
    runner_id: str, request: RunnerRetireRequest, services: Annotated[HubServices, Depends(get_services)]
) -> RunnerRetireResponse:
    """Retire a runner — its token is revoked and its claims and registrations refused;
    409 naming each held chunk unless ``force``, which releases them. Re-running on a
    retired runner re-runs the release pass."""
    registration = _registration(services, runner_id)
    held = services.chunks.route.live_routes_of_runner(runner_id)
    changes = chunk_events.ChunkChanged.before_many(services, [r.chunk_id for r in held])
    try:
        outcome = services.fleet.retire(registration, by=request.by, force=request.force)
    except RunnerHoldsRoutes as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    for released in outcome.released:
        change = changes.get(released.chunk_id) or chunk_events.ChunkChanged.before(services, released.chunk_id)
        change.publish(cause="detached", key=f"route_released:{released.released_id}")
    if outcome.released:
        services.events.publish_queue_changed()  # released chunks re-enter the ready queue
    if outcome.fact_id is not None:
        services.events.publish_runner_changed(
            runner_id,
            kind="retired",
            runner_name=registration.name,
            by=request.by,
            key=f"runner_lifecycle_facts:{outcome.fact_id}",
        )
    return RunnerRetireResponse(
        runner=_reread(services, runner_id), released_chunk_ids=[r.chunk_id for r in outcome.released]
    )


@router.post(
    "/runners/{runner_id}/reinstate", response_model=RunnerRegistryView, dependencies=[Depends(require(RUNNER_RETIRE))]
)
def reinstate_runner(
    runner_id: str, request: RunnerLifecycleRequest, services: Annotated[HubServices, Depends(get_services)]
) -> RunnerRegistryView:
    """Reinstate a retired runner — it stays unenrolled until enrolled afresh; 409 when not retired."""
    registration = _registration(services, runner_id)
    try:
        fact_id = services.fleet.reinstate(registration, by=request.by)
    except RunnerNotRetired as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    services.events.publish_runner_changed(
        runner_id,
        kind="reinstated",
        runner_name=registration.name,
        by=request.by,
        key=f"runner_lifecycle_facts:{fact_id}",
    )
    return _reread(services, runner_id)


@router.post(
    "/runners/{runner_id}/token-revocations",
    response_model=RunnerTokenRevocationResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(RUNNER_RETIRE))],
)
def revoke_runner_token(
    runner_id: str, request: RunnerLifecycleRequest, services: Annotated[HubServices, Depends(get_services)]
) -> RunnerTokenRevocationResponse:
    """Revoke a runner's token — it stays added and must be re-enrolled; 409 when unenrolled."""
    registration = _registration(services, runner_id)
    try:
        revocation_id = services.fleet.revoke_token(registration, by=request.by)
    except RunnerNotEnrolled as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    services.events.publish_runner_changed(
        runner_id,
        kind="token-revoked",
        runner_name=registration.name,
        by=request.by,
        key=f"runner_token_revocations:{revocation_id}",
    )
    return RunnerTokenRevocationResponse(runner=_reread(services, runner_id))
