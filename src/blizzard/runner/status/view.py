"""The runner's machine-local status view (``bzh:domain-core``).

This runner's own capacities, environment pool, open asks, and parked escalations, all
derived from store facts at read time (``bzh:facts-not-status``). Hub *reachability* has
no fact of its own, so it is derived from how stale ``hub_contact_at`` reads against
``now``. Escalation resume commands are **recomputed**, never read off the outbound tail."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import domain_model
from blizzard.runner.environments.repository import (
    EnvBinding,
    IReadEnvironmentRepository,
    group_bindings_by_chunk,
)
from blizzard.runner.harness.registry import IHarnessLifecycleRegistry, UnavailableHarnessError, UnknownHarnessError
from blizzard.runner.hub.identity import ICurrentRunnerIdentity
from blizzard.runner.hub.outbound_buffer import IReadOutboundRepository, OutboundFactEntry
from blizzard.runner.leases.asks import IReadAskRepository, OpenAsk
from blizzard.runner.leases.escalations import IReadEscalationRepository
from blizzard.runner.leases.record import IReadLeaseRecordRepository
from blizzard.runner.lifecycle.takeover import EscalationCommands, IReadTakeoverRepository
from blizzard.runner.throttle.pause import IReadPauseRepository, RunnerBrakes

__all__ = [
    "HUB_CONTACT_STALENESS_THRESHOLD",
    "Capacities",
    "EnvironmentSlot",
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
class Capacities:
    """See ``src/blizzard/wire/runner_status.py``'s ``CapacitiesView``."""

    max_agents: int
    used: int
    free: int

    @classmethod
    def of(cls, *, max_agents: int, used: int) -> Capacities:
        """``used`` of ``max_agents`` slots; ``free`` never reads below zero, even when more
        leases are active than the configured maximum allows."""
        return cls(max_agents=max_agents, used=used, free=max(max_agents - used, 0))


@domain_model
@dataclass(frozen=True)
class HubConnectivity:
    """Hub reachability, derived from staleness, plus the outbound backlog depth.

    ``endpoint`` is the configured hub base URL — identity config, not a probe result;
    the local panel's one handle on where the fleet board lives."""

    endpoint: str
    reachable: bool
    last_contact_at: datetime | None
    buffer_depth: int

    @classmethod
    def of(
        cls,
        *,
        endpoint: str,
        contact_at: datetime | None,
        buffer_depth: int,
        now: datetime,
        threshold: timedelta = HUB_CONTACT_STALENESS_THRESHOLD,
    ) -> HubConnectivity:
        """Reachable when the last successful contact is no older than ``threshold`` as of
        ``now``; never reachable before a first contact."""
        reachable = contact_at is not None and (now - contact_at) <= threshold
        return cls(endpoint=endpoint, reachable=reachable, last_contact_at=contact_at, buffer_depth=buffer_depth)


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


@domain_model
@dataclass(frozen=True)
class EnvironmentSlot:
    """One environment in the runner's configured pool. Every pool
    environment surfaces, held or not: ``chunk_id``/``held_since`` are set only while the
    environment is bound, ``None`` otherwise — never an invented ref for an idle slot."""

    environment_id: str
    chunk_id: str | None
    held_since: datetime | None

    def is_held(self) -> bool:
        """Whether a chunk holds this environment now — the one reading of "held" every
        surface shows, rather than each re-deriving it from ``chunk_id``."""
        return self.chunk_id is not None

    @classmethod
    def pool_view(cls, env_pool: Sequence[str], held_bindings: Sequence[EnvBinding]) -> list[EnvironmentSlot]:
        """The full configured pool, joined against the held bindings, in pool order.

        A bound environment never silently vanishes: each extra binding on a pool id (``environment_id``
        is not unique) gets its own row after the slot, and a binding off the pool is appended at the end."""
        held_by_env: dict[str, list[EnvBinding]] = {}
        for binding in held_bindings:
            held_by_env.setdefault(binding.environment_id, []).append(binding)
        slots: list[EnvironmentSlot] = []
        for env_id in env_pool:
            bindings = held_by_env.get(env_id, [])
            primary = bindings[0] if bindings else None
            slots.append(
                cls(
                    environment_id=env_id,
                    chunk_id=primary.chunk_id if primary else None,
                    held_since=primary.bound_at if primary else None,
                )
            )
            slots.extend(cls._held(extra) for extra in bindings[1:])
        pool = set(env_pool)
        for env_id, bindings in held_by_env.items():
            if env_id not in pool:
                slots.extend(cls._held(binding) for binding in bindings)
        return slots

    @classmethod
    def _held(cls, binding: EnvBinding) -> EnvironmentSlot:
        return cls(environment_id=binding.environment_id, chunk_id=binding.chunk_id, held_since=binding.bound_at)


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
        lease_record: IReadLeaseRecordRepository,
        outbound: IReadOutboundRepository,
        environments: IReadEnvironmentRepository,
        asks: IReadAskRepository,
        takeover: IReadTakeoverRepository,
        escalations: IReadEscalationRepository,
        identity: ICurrentRunnerIdentity,
        runner_name: str,
        workspace_id: str,
        max_agents: int,
        hub_url: str,
        env_pool: tuple[str, ...],
        harnesses: IHarnessLifecycleRegistry,
        workspace_root: str,
        gates: tuple[str, ...] = (),
        runner_dir: str = "",
        contact_staleness: timedelta = HUB_CONTACT_STALENESS_THRESHOLD,
    ) -> None:
        self._pause = pause
        self._lease_record = lease_record
        self._outbound = outbound
        self._environments = environments
        self._asks = asks
        self._takeover = takeover
        self._escalations = escalations
        self._workspace_root = workspace_root
        self._clock = clock
        self._harnesses = harnesses
        self._identity = identity
        self._runner_name = runner_name
        self._workspace_id = workspace_id
        self._max_agents = max_agents
        self._hub_url = hub_url
        self._env_pool = env_pool
        self._gates = gates
        self._runner_dir = runner_dir
        self._contact_staleness = contact_staleness

    def summary(self) -> RunnerStatusSummary:
        local_paused = self._pause.local_paused()
        hub_paused = self._pause.hub_paused()
        local_reason = self._pause.local_pause_reason() if local_paused else None
        identity = self._identity.current()
        return RunnerStatusSummary(
            runner_id=identity.runner_id if identity is not None else None,
            runner_name=identity.runner_name if identity is not None else self._runner_name,
            workspace_id=self._workspace_id,
            pause=PauseState.of(local=local_paused, hub=hub_paused, local_reason=local_reason),
            capacities=Capacities.of(max_agents=self._max_agents, used=len(self._lease_record.list_active_leases())),
            hub=HubConnectivity.of(
                endpoint=self._hub_url,
                contact_at=self._pause.hub_contact_at(),
                buffer_depth=self._outbound.pending_outbound_count(),
                now=self._clock.now(),
                threshold=self._contact_staleness,
            ),
            last_tick_at=self._pause.last_daemon_liveness(),
            gates=self._gates,
        )

    def environments(self) -> list[EnvironmentSlot]:
        """The full configured pool, joined against the held binding facts.
        A bound environment never silently vanishes: a binding whose id has fallen out of
        the pool still surfaces, and — since ``env_bindings`` has no unique constraint on
        ``environment_id`` — so does every extra binding past the first on one id."""
        return EnvironmentSlot.pool_view(self._env_pool, self._environments.held_bindings())

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
            commands = EscalationCommands.compose(
                escalation.chunk_id,
                session=session,
                bindings=held_by_chunk.get(escalation.chunk_id, []),
                harness=harness,
                model=escalation.resolved_model,
                effort=escalation.resolved_effort,
                workspace_root=self._workspace_root,
                runner_dir=self._runner_dir,
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
