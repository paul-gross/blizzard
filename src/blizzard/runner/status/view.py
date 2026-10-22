"""The runner's machine-local status view (``bzh:domain-core``).

This runner's own capacities, open asks, and parked escalations, all
derived from store facts at read time (``bzh:facts-not-status``). Hub *reachability* has
no fact of its own, so it is derived from how stale ``hub_contact_at`` reads against
``now``. Escalation resume commands are **recomputed**, never read off the outbound tail."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import domain_model
from blizzard.runner.environments.repository import (
    EnvBinding,
    IReadEnvironmentRepository,
    OpenLeaseHold,
    group_bindings_by_chunk,
)
from blizzard.runner.environments.workspaces import IWorkspaceSpawnRoots
from blizzard.runner.harness.registry import IHarnessLifecycleRegistry, UnavailableHarnessError, UnknownHarnessError
from blizzard.runner.hub.client import BindingDeclaration
from blizzard.runner.hub.hub_version import HubVersion, ICurrentHubVersion
from blizzard.runner.hub.identity import ICurrentIdentityVerdict, ICurrentRunnerIdentity, IdentityVerdict
from blizzard.runner.hub.outbound_buffer import IReadOutboundRepository, OutboundFactEntry
from blizzard.runner.hub.served_projects import (
    IReadServedProjectsRepository,
    ServedProjectStatus,
    served_projects_status,
)
from blizzard.runner.leases.asks import IReadAskRepository, OpenAsk
from blizzard.runner.leases.escalations import IReadEscalationRepository
from blizzard.runner.leases.record import IReadLeaseRecordRepository
from blizzard.runner.lifecycle.daemon_liveness import IReadDaemonLivenessRepository
from blizzard.runner.lifecycle.model import SlotBinding, binding_usage, free_slots, open_lease_count
from blizzard.runner.lifecycle.routing import RoutingConflict, RoutingMap, routing_map
from blizzard.runner.lifecycle.takeover import EscalationCommands, IReadTakeoverRepository
from blizzard.runner.throttle.pause import IReadPauseRepository, RunnerBrakes

__all__ = [
    "HUB_CONTACT_STALENESS_THRESHOLD",
    "BindingCapacity",
    "Capacities",
    "EscalationView",
    "HubConnectivity",
    "OpenTakeoverView",
    "PauseState",
    "RunnerStatusService",
    "RunnerStatusSummary",
]

#: How stale the last successful hub contact may read before the summary calls the hub
#: unreachable — generous, so a single slow tick never flips this false.
HUB_CONTACT_STALENESS_THRESHOLD = timedelta(minutes=5)


@domain_model
@dataclass(frozen=True)
class PauseState:
    """The pause brake's two independent surfaces, plus their effective OR.

    Reported apart because they are cleared by different verbs
    (``blizzard runner start`` vs. ``blizzard hub runner resume``). ``local_reason`` is the
    local brake's own reason (a usage limit, the spend ceiling), ``None`` on a plain operator
    pause or when the brake is not engaged locally at all."""

    local: bool
    hub: bool
    effective: bool
    local_reason: str | None

    @classmethod
    def of(cls, *, local: bool, hub: bool, local_reason: str | None) -> PauseState:
        """The two brakes as read, their effective state, and the local reason — kept only
        while the local brake is engaged."""
        return cls(
            local=local,
            hub=hub,
            effective=RunnerBrakes(local=local, hub=hub).effective,
            local_reason=local_reason if local else None,
        )


@domain_model
@dataclass(frozen=True)
class BindingCapacity:
    """One binding's agent slots: the entry as declared, the project id it resolved to (``None`` while it routes
    nothing), its workspace, and its ``agents`` split into ``used`` and ``free``."""

    project: str
    project_id: str | None
    workspace_id: str
    agents: int
    used: int
    free: int


@domain_model
@dataclass(frozen=True)
class Capacities:
    """See ``src/blizzard/wire/runner_status.py``'s ``CapacitiesView``."""

    used: int
    agents: int = 0
    free: int = 0
    bindings: tuple[BindingCapacity, ...] = ()

    @classmethod
    def of(
        cls,
        bindings: Sequence[BindingDeclaration],
        routing: RoutingMap,
        holds: Sequence[OpenLeaseHold],
        agents: int,
    ) -> Capacities:
        """The runner's ``agents`` against its open leases, and each binding's use. Every agent is shared, so a
        binding's ``free`` is the runner's; it never reads below zero, even when more leases are open than
        ``agents``."""
        open_leases = open_lease_count(holds)
        free = free_slots(agents, open_leases)
        resolved = [routing.project_id(binding.project) for binding in bindings]
        used = binding_usage(
            [SlotBinding(workspace_id=b.workspace_id, project_id=p) for b, p in zip(bindings, resolved, strict=True)],
            holds,
        )
        return cls(
            used=open_leases,
            agents=agents,
            free=free,
            bindings=tuple(
                BindingCapacity(
                    project=binding.project,
                    project_id=project_id,
                    workspace_id=binding.workspace_id,
                    agents=binding.agents,
                    used=in_use,
                    free=free,
                )
                for binding, project_id, in_use in zip(bindings, resolved, used, strict=True)
            ),
        )


@domain_model
@dataclass(frozen=True)
class HubConnectivity:
    """Hub reachability, derived from staleness, plus the outbound backlog depth.

    ``endpoint`` is the configured hub base URL — identity config, not a probe result;
    the local panel's one handle on where the fleet board lives. ``version`` is the latest registration reply's."""

    endpoint: str
    reachable: bool
    last_contact_at: datetime | None
    buffer_depth: int
    version: HubVersion = field(default_factory=HubVersion)

    @classmethod
    def of(
        cls,
        *,
        endpoint: str,
        contact_at: datetime | None,
        buffer_depth: int,
        now: datetime,
        threshold: timedelta = HUB_CONTACT_STALENESS_THRESHOLD,
        version: HubVersion | None = None,
    ) -> HubConnectivity:
        """Reachable when the last successful contact is no older than ``threshold`` as of
        ``now``; never reachable before a first contact."""
        reachable = contact_at is not None and (now - contact_at) <= threshold
        return cls(
            endpoint=endpoint,
            reachable=reachable,
            last_contact_at=contact_at,
            buffer_depth=buffer_depth,
            version=version if version is not None else HubVersion(),
        )


@domain_model
@dataclass(frozen=True)
class RunnerStatusSummary:
    """Identity, pause state, capacities, hub connectivity, and last tick — ``GET /runner``."""

    #: The hub-minted id of the latest registration; ``None`` before the first.
    runner_id: str | None
    #: The name the hub recorded at that registration, else the configured name.
    runner_name: str
    workspace_id: str
    pause: PauseState
    capacities: Capacities
    hub: HubConnectivity
    last_tick_at: datetime | None
    #: The node names this runner's loaded configuration holds for a human decision.
    gates: tuple[str, ...] = ()
    #: Whether the token resolves to the configured runner; ``None`` where no loop checks it.
    identity: IdentityVerdict | None = None
    #: The tenant the runner serves, as its hub last answered; ``None`` until a hub that names it answers.
    tenant_id: str | None = None
    #: That tenant's name, as of the same answer.
    tenant_name: str | None = None
    #: The projects the runner serves, as its hub last answered, then each configured entry it has not
    #: answered yet; ``None`` when neither the configuration nor an answer names any.
    served_projects: tuple[ServedProjectStatus, ...] | None = None
    #: Each held binding no workspace has claimed — its workdir matched none the provider lists. It
    #: keeps its lease and still counts against every binding of the runner.
    unmatched_bindings: tuple[EnvBinding, ...] = ()
    #: Each project two or more bindings resolve to; while any stands, the runner claims nothing.
    routing_conflicts: tuple[RoutingConflict, ...] = ()


@domain_model
@dataclass(frozen=True)
class EscalationView:
    """One parked escalation with its literal, ready-to-paste resume command. The
    session's own configuration rides beside it — its declared pool and the model/effort
    it ran under. All three are ``None`` for a session on the bare
    vocabulary, which belongs to no pool, or one predating the stamps."""

    chunk_id: str
    lease_id: str
    node_id: str
    epoch: int
    closed_at: datetime
    resume_command: str
    session_name: str | None = None
    model: str | None = None
    effort: str | None = None
    harness_id: str | None = None
    harness_version: str | None = None
    wrapped_takeover_command: str | None = None
    cause: str | None = None


@domain_model
@dataclass(frozen=True)
class OpenTakeoverView:
    """One open operator takeover — the recovery surface
    for a takeover a stranded client left open with no other way to find its
    ``takeover_id``."""

    chunk_id: str
    takeover_id: str
    held_since: datetime
    harness_id: str | None = None


class RunnerStatusService:
    """Composition-root-wired: the store, clock, harness registry, and this runner's own
    identity/config — everything ``blizzard runner status`` renders."""

    def __init__(
        self,
        clock: IClock,
        *,
        pause: IReadPauseRepository,
        daemon_liveness: IReadDaemonLivenessRepository,
        lease_record: IReadLeaseRecordRepository,
        outbound: IReadOutboundRepository,
        environments: IReadEnvironmentRepository,
        asks: IReadAskRepository,
        takeover: IReadTakeoverRepository,
        escalations: IReadEscalationRepository,
        identity: ICurrentRunnerIdentity,
        identity_verdict: ICurrentIdentityVerdict | None = None,
        hub_version: ICurrentHubVersion | None = None,
        served_projects: IReadServedProjectsRepository | None = None,
        projects: tuple[str, ...] | None = None,
        runner_name: str,
        workspace_id: str,
        bindings: tuple[BindingDeclaration, ...] = (),
        agents: int = 0,
        hub_url: str,
        harnesses: IHarnessLifecycleRegistry,
        workspaces: IWorkspaceSpawnRoots,
        gates: tuple[str, ...] = (),
        runner_dir: str = "",
        contact_staleness: timedelta = HUB_CONTACT_STALENESS_THRESHOLD,
    ) -> None:
        self._pause = pause
        self._daemon_liveness = daemon_liveness
        self._lease_record = lease_record
        self._outbound = outbound
        self._environments = environments
        self._asks = asks
        self._takeover = takeover
        self._escalations = escalations
        self._workspaces = workspaces
        self._clock = clock
        self._harnesses = harnesses
        self._identity = identity
        self._identity_verdict = identity_verdict
        self._hub_version = hub_version
        self._served_projects = served_projects
        self._projects = projects
        self._runner_name = runner_name
        self._workspace_id = workspace_id
        self._bindings = bindings
        self._agents = agents
        self._hub_url = hub_url
        self._gates = gates
        self._runner_dir = runner_dir
        self._contact_staleness = contact_staleness

    def summary(self) -> RunnerStatusSummary:
        local_paused = self._pause.local_paused()
        hub_paused = self._pause.hub_paused()
        local_reason = self._pause.local_pause_reason() if local_paused else None
        identity = self._identity.current()
        answer = self._served_projects.served_projects_answer() if self._served_projects is not None else None
        routing = routing_map(self._bindings, answer)
        return RunnerStatusSummary(
            runner_id=identity.runner_id if identity is not None else None,
            runner_name=identity.runner_name if identity is not None else self._runner_name,
            workspace_id=self._workspace_id,
            pause=PauseState.of(local=local_paused, hub=hub_paused, local_reason=local_reason),
            capacities=Capacities.of(self._bindings, routing, self._environments.open_lease_holds(), self._agents),
            hub=HubConnectivity.of(
                endpoint=self._hub_url,
                contact_at=self._pause.hub_contact_at(),
                buffer_depth=self._outbound.pending_outbound_count(),
                now=self._clock.now(),
                threshold=self._contact_staleness,
                version=self._hub_version.current() if self._hub_version is not None else None,
            ),
            last_tick_at=self._daemon_liveness.last_daemon_liveness(),
            gates=self._gates,
            identity=self._identity_verdict.verdict() if self._identity_verdict is not None else None,
            tenant_id=identity.tenant_id if identity is not None else None,
            tenant_name=identity.tenant_name if identity is not None else None,
            served_projects=served_projects_status(self._projects, answer),
            unmatched_bindings=tuple(b for b in self._environments.held_bindings() if b.workspace_id is None),
            routing_conflicts=routing.conflicts,
        )

    def open_asks(self) -> list[OpenAsk]:
        return self._asks.open_asks()

    def recent_facts(self, limit: int) -> list[OutboundFactEntry]:
        """The newest hub-bound facts, acked or not — the local panel's fact log."""
        return self._outbound.recent_outbound(limit)

    def open_takeovers(self) -> list[OpenTakeoverView]:
        return [
            OpenTakeoverView(
                chunk_id=t.chunk_id, takeover_id=t.takeover_id, held_since=t.opened_at, harness_id=t.harness_id
            )
            for t in self._takeover.open_takeovers()
        ]

    def escalations(self) -> list[EscalationView]:
        held_by_chunk = group_bindings_by_chunk(self._environments.held_bindings())
        views = []
        for escalation in self._escalations.open_escalations():
            session = escalation.session
            harness = None
            if session is not None:
                try:
                    harness = self._harnesses.lifecycle(session.harness_id)
                except (UnknownHarnessError, UnavailableHarnessError):
                    # The escalation remains visible under its recorded owner, but cannot
                    # offer a command this runner cannot compose. A polled read: no log.
                    harness = None
            bindings = held_by_chunk.get(escalation.chunk_id, [])
            commands = EscalationCommands.compose(
                escalation.chunk_id,
                session=session,
                bindings=bindings,
                harness=harness,
                model=escalation.resolved_model,
                effort=escalation.resolved_effort,
                workspace_root=self._workspaces.spawn_root(bindings[0].workspace_id if bindings else None),
                runner_dir=self._runner_dir,
                runner=self._runner_name,
            )
            views.append(
                EscalationView(
                    chunk_id=escalation.chunk_id,
                    lease_id=escalation.lease_id,
                    node_id=escalation.node_id,
                    epoch=escalation.epoch,
                    closed_at=escalation.closed_at,
                    resume_command=commands.resume,
                    session_name=escalation.session_name,
                    model=escalation.resolved_model,
                    effort=escalation.resolved_effort,
                    harness_id=escalation.harness_id,
                    harness_version=escalation.harness_version,
                    wrapped_takeover_command=commands.wrapped,
                    cause=escalation.cause,
                )
            )
        return views
