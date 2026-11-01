"""The fleet service — registration, liveness, the pause brake, and a retire that releases chunk routes."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.chunk.model import Chunk
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.chunk.ports.route import IReadChunkRouteRepository
from blizzard.hub.domain.execution.detach import DetachService, held_routes
from blizzard.hub.domain.runners.registration import (
    STALE_AFTER,
    DeclaredSubscription,
    IWriteRunnerRegistry,
    RetiredRunnerGuard,
    RunnerBinding,
    RunnerCapability,
    RunnerDeclining,
    RunnerLiveness,
    RunnerNotEnrolled,
    RunnerRegistration,
    RunnerReport,
    ServedEntry,
    declared_name,
)
from blizzard.hub.domain.runners.route import Route
from blizzard.hub.domain.runners.served import IServedProjects, declaration_at_registration
from blizzard.hub.domain.runners.version import IVersionWindow, RunnerVersionRefused, VersionJudgement

_log = get_logger("blizzard.hub.registry")


@domain_model
@dataclass(frozen=True)
class ReleasedRoute:
    """One route the retire release pass released — the chunk and its ``route_released.id``."""

    chunk_id: str
    released_id: int


@domain_model
@dataclass(frozen=True)
class Registered:
    """What one registration recorded: whether it was the runner's first since it was added, the
    name the runner now holds — the one it declared, else the one it already held — and the served
    declaration it now holds, the one it sent or else the stored one. ``changed`` is whether the registration
    altered what the hub held for the runner, or found it offline — a bare liveness refresh is not a change."""

    first: bool
    name: str
    projects: tuple[ServedEntry, ...]
    changed: bool
    #: The window's judgement of the version it reported.
    version: VersionJudgement
    #: The bindings and subscription the runner now holds — what it sent, ``None`` included.
    bindings: tuple[RunnerBinding, ...] | None = None
    subscription: str | None = None


@domain_model
@dataclass(frozen=True)
class RetireOutcome:
    """What one retire wrote: ``fact_id`` is ``None`` on a re-run over an already-retired
    runner, ``revocation_id`` ``None`` when no token was enrolled."""

    fact_id: int | None
    revocation_id: int | None
    released: tuple[ReleasedRoute, ...]


class FleetService:
    """Register runners, refresh liveness, set the declarative pause brake, and retire."""

    def __init__(
        self,
        *,
        registry: IWriteRunnerRegistry,
        routes: IReadChunkRouteRepository,
        records: IReadChunkRecordRepository,
        facts: IReadChunkFactsRepository,
        detach: DetachService,
        retired: RetiredRunnerGuard,
        served_projects: IServedProjects,
        versions: IVersionWindow,
        clock: IClock,
        stale_after: timedelta = STALE_AFTER,
    ) -> None:
        self._registry = registry
        # The tenant's projects, which a served declaration resolves against.
        self._served_projects = served_projects
        # Retirement's holdings read and release pass — the hub's existing detach path.
        self._routes = routes
        self._records = records
        self._facts = facts
        self._detach = detach
        self._retired = retired
        self._versions = versions
        self._clock = clock
        self._stale_after = stale_after

    @property
    def registry(self) -> IWriteRunnerRegistry:
        return self._registry

    def register(
        self,
        held: RunnerRegistration,
        workspace_id: str,
        *,
        name: str | None = None,
        env_capacity: int | None = None,
        public_url: str | None = None,
        redirect_uris: tuple[str, ...] = (),
        capabilities: tuple[RunnerCapability, ...] = (),
        subscriptions: tuple[DeclaredSubscription, ...] | None = None,
        gates: tuple[str, ...] = (),
        projects: Sequence[str] | None = None,
        bindings: tuple[RunnerBinding, ...] | None = None,
        subscription: str | None = None,
        agents: int | None = None,
        declining: tuple[RunnerDeclining, ...] | None = None,
        version: str | None = None,
    ) -> Registered:
        """Register (or refresh) ``held``, a runner the hub added — the one its bearer token names.

        The runner's reported facts are overwritten on every registration; absent values store as
        null/empty. A declared ``name`` replaces the one held; none, or a blank one, keeps it. A sent
        ``projects`` replaces the served declaration (``[]`` clears it); ``None`` keeps it, re-resolving
        only its unresolved entries. Sent ``bindings`` replace ``projects`` as the declaration, each binding's project
        in binding order; ``bindings``, ``subscription``, ``agents`` and ``declining`` are stored as sent. No entry is
        refused: a
        registration is also the runner's heartbeat. A retired runner raises
        :class:`~blizzard.hub.domain.runners.registration.RunnerRetired` before any write. A ``version`` the window
        refuses records only the judgement, then raises :class:`RunnerVersionRefused`."""
        held.refuse_if_retired(action="registration")
        runner_id = held.runner_id
        now = self._clock.now()
        judgement = self._versions.judge(version)
        version_changed = held.version_changed(judgement)
        refusal = judgement.refusal
        if refusal is not None:
            self._registry.record_version_refusal(
                runner_id, version=judgement.reported_version, reason=refusal.reason, at=now
            )
            _log.info(
                "runner registration refused on version",
                runner_id=runner_id,
                version=version,
                reason=refusal.reason,
                hub_version=judgement.hub_version,
                floor=judgement.floor,
            )
            raise RunnerVersionRefused(runner_id, judgement, refusal, changed=version_changed)
        recorded_name = declared_name(name)
        declared = projects if bindings is None else [b.project for b in bindings]
        served = declaration_at_registration(held.served, declared, self._served_projects)
        changed = held.registration_changes(
            RunnerReport(
                name=recorded_name,
                workspace_id=workspace_id,
                env_capacity=env_capacity,
                public_url=public_url,
                redirect_uris=redirect_uris,
                capabilities=capabilities,
                subscriptions=subscriptions,
                gates=gates,
                bindings=bindings,
                subscription=subscription,
                agents=agents,
                declining=declining,
            ),
            served=served,
            judgement=judgement,
            now=now,
            threshold=self._stale_after,
        )
        created = self._registry.record_registration(
            runner_id,
            name=recorded_name,
            workspace_id=workspace_id,
            env_capacity=env_capacity,
            public_url=public_url,
            redirect_uris=redirect_uris,
            capabilities=capabilities,
            subscriptions=subscriptions,
            gates=gates,
            projects=served,
            bindings=bindings,
            subscription=subscription,
            agents=agents,
            declining=declining,
            version=judgement.reported_version,
            at=now,
        )
        _log.info(
            "runner registered",
            runner_id=runner_id,
            name=recorded_name or held.name,
            workspace_id=workspace_id,
            env_capacity=env_capacity,
            public_url=public_url,
            capabilities=[c.harness_id for c in capabilities],
            subscriptions=None if subscriptions is None else [s.slug for s in subscriptions],
            projects=None if served is None else [e.entry for e in served],
            first_time=created,
        )
        return Registered(
            first=created,
            name=recorded_name or held.name,
            projects=held.served if served is None else served,
            changed=changed or created,
            version=judgement,
            bindings=bindings,
            subscription=subscription,
        )

    def heartbeat(self, runner_id: str) -> bool:
        """Refresh a runner's liveness; returns False if it is unregistered. A retired runner
        is refused with :class:`~blizzard.hub.domain.runners.registration.RunnerRetired` first."""
        self._retired.refuse_if_retired(runner_id, action="heartbeat")
        return self._registry.touch_last_seen(runner_id, at=self._clock.now())

    def retire(self, registration: RunnerRegistration, *, by: str, force: bool) -> RetireOutcome:
        """Record the fact and revoke the token first, so claims are refused from that instant,
        then release every held route through ``DetachService`` — a terminal chunk holds none.
        The registration decides the fact (:meth:`RunnerRegistration.retire`); a re-run writes
        none and re-runs the release pass, which also catches a claim that slipped past the
        pre-lock holdings read."""
        runner_id = registration.runner_id
        now = self._clock.now()
        fact = registration.retire(self.holdings(runner_id), force=force, by=by, at=now)
        fact_id = None
        if fact is not None:
            fact_id = self._registry.record_lifecycle(runner_id, retired=fact.retired, at=fact.at, by=fact.by)
        revocation_id = self._registry.revoke_token(runner_id, at=now, by=by)
        routes = self._routes.live_routes_of_runner(runner_id)
        records = self._records.get_many([route.chunk_id for route in routes])
        released = tuple(self._release(route, records.get(route.chunk_id)) for route in routes)
        outcome = RetireOutcome(
            fact_id=fact_id, revocation_id=revocation_id, released=tuple(r for r in released if r is not None)
        )
        _log.info(
            "runner retired",
            runner_id=runner_id,
            by=by,
            force=force,
            rerun=fact_id is None,
            released=[r.chunk_id for r in outcome.released],
        )
        return outcome

    def holdings(self, runner_id: str) -> list[Route]:
        """The runner's live routes on chunks that still hold a claim."""
        routes = self._routes.live_routes_of_runner(runner_id)
        facts = self._facts.status_facts_for([route.chunk_id for route in routes])
        return held_routes(routes, {chunk_id: f.status() for chunk_id, f in facts.items()})

    def _release(self, route: Route, chunk: Chunk | None) -> ReleasedRoute | None:
        if chunk is None:  # pragma: no cover - a routed chunk always has its record
            return None
        released_id = self._detach.release_held(chunk, runner_id=route.runner_id)
        if released_id is None:  # released (or re-claimed elsewhere) since the holdings read
            return None
        return ReleasedRoute(chunk_id=route.chunk_id, released_id=released_id)

    def reinstate(self, registration: RunnerRegistration, *, by: str) -> int:
        """Record a ``retired=False`` fact, returning its id. The runner stays unenrolled —
        its token was revoked at retire — so the operator enrolls it afresh."""
        fact = registration.reinstate(by=by, at=self._clock.now())
        fact_id = self._registry.record_lifecycle(fact.runner_id, retired=fact.retired, at=fact.at, by=fact.by)
        _log.info("runner reinstated", runner_id=registration.runner_id, by=by)
        return fact_id

    def revoke_token(self, registration: RunnerRegistration, *, by: str) -> int:
        """Revoke the runner's current token, leaving it added; returns the revocation id.
        Refuses with :class:`RunnerNotEnrolled` when it holds none."""
        revocation = registration.revoke_token(by=by, at=self._clock.now())
        revocation_id = self._registry.revoke_token(revocation.runner_id, at=revocation.at, by=revocation.by)
        if revocation_id is None:  # revoked concurrently between the read and the write
            raise RunnerNotEnrolled(registration.runner_id)
        _log.info("runner token revoked", runner_id=registration.runner_id, by=by)
        return revocation_id

    def set_paused(self, registration: RunnerRegistration, *, paused: bool, by: str) -> int:
        """Flip the fleet's brake for an added runner, connected or not, returning the freshly-written
        ``runner_pause_facts.id``. Takes the loaded registration (``bzh:domain-takes-objects``)."""
        fact_id = self._registry.record_pause(registration.runner_id, paused=paused, at=self._clock.now(), by=by)
        _log.info("runner pause set", runner_id=registration.runner_id, paused=paused, by=by)
        return fact_id

    def record_local_pause(
        self, runner_id: str, *, paused: bool, at: datetime, by: str, reason: str | None = None
    ) -> int:
        """Land a runner's report that it paused or started *itself* — not a control: the
        runner has already stopped claiming, and the hub cannot set this brake. ``reason``
        carries the fact's own composed cause, ``None`` for a manual pause and always on a start. Unlike
        ``set_paused`` this does not require a known runner: the buffer replays an outage in FIFO order,
        so a pause can legitimately arrive before the registration that follows it."""
        fact_id = self._registry.record_local_pause(runner_id, paused=paused, at=at, by=by, reason=reason)
        _log.info("runner local pause reported", runner_id=runner_id, paused=paused, by=by, reason=reason)
        return fact_id

    def record_external_usage(
        self, runner_id: str, *, slug: str, name: str, sampled_at: datetime, windows_json: str, at: datetime
    ) -> None:
        """Land one declared subscription's reported usage sample —
        refresh-in-place per ``(runner_id, slug)``, with no known runner required
        (:meth:`record_local_pause`)."""
        self._registry.record_external_usage(
            runner_id, slug=slug, name=name, sampled_at=sampled_at, windows_json=windows_json, at=at
        )
        _log.info("runner external usage sample landed", runner_id=runner_id, slug=slug, sampled_at=sampled_at)

    def record_external_usage_miss(
        self, runner_id: str, *, slug: str, name: str, missed_at: datetime, reason: str, at: datetime
    ) -> None:
        """Land one declared subscription's reported miss — refresh-in-place
        per ``(runner_id, slug)``, with no known runner required (:meth:`record_local_pause`)."""
        self._registry.record_external_usage_miss(
            runner_id, slug=slug, name=name, missed_at=missed_at, reason=reason, at=at
        )
        _log.info(
            "runner external usage miss landed", runner_id=runner_id, slug=slug, reason=reason, missed_at=missed_at
        )

    def get_liveness(self, registration: RunnerRegistration) -> RunnerLiveness:
        """One runner's derived liveness over its loaded registration (``bzh:domain-takes-objects``)."""
        return self._liveness(registration)

    def own_liveness(self, registration: RunnerRegistration) -> RunnerLiveness:
        """The runner's own pull read — refused with :class:`~blizzard.hub.domain.runners.registration.RunnerRetired`
        when retired, unlike the operator's :meth:`get_liveness`, which still shows it."""
        registration.refuse_if_retired(action="runner read")
        return self._liveness(registration)

    def list_with_liveness(self, *, now: datetime, include_retired: bool = False) -> list[RunnerLiveness]:
        """Every added runner, oldest first and never-connected ones included, with its derived
        liveness as of the caller's ``now``; retired runners only when
        ``include_retired``."""
        return [
            RunnerLiveness.of(r, now=now, threshold=self._stale_after)
            for r in self._registry.list_runners(include_retired=include_retired)
        ]

    def _liveness(self, registration: RunnerRegistration) -> RunnerLiveness:
        return RunnerLiveness.of(registration, now=self._clock.now(), threshold=self._stale_after)
