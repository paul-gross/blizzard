"""The runner's machine-local status view (``bzh:domain-core``, issue #51).

This runner's own capacities, environment pool, open asks, and parked escalations, all
derived from store facts at read time (``bzh:facts-not-status``). Hub *reachability* has
no fact of its own, so it is derived from how stale ``hub_contact_at`` reads against
``now``. Escalation resume commands are **recomputed**, never read off the outbound tail."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from blizzard.foundation.clock import IClock
from blizzard.runner.harness.adapter import IHarnessAdapter
from blizzard.runner.store.repository import AskRecord, EnvBindingRecord, IReadRunnerStore, OutboundFactRecord

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


@dataclass(frozen=True)
class PauseState:
    """The pause brake's two independent surfaces, plus their effective OR.

    Reported apart because they are cleared by different verbs
    (``blizzard runner start`` vs. ``blizzard hub runner resume``)."""

    local: bool
    hub: bool
    effective: bool


@dataclass(frozen=True)
class Capacities:
    """Agent slots — the same math FILL claims against (``loop/steps.py``'s ``Fill``)."""

    max_agents: int
    used: int
    free: int


@dataclass(frozen=True)
class HubConnectivity:
    """Hub reachability, derived from staleness, plus the outbound backlog depth.

    ``endpoint`` is the configured hub base URL — identity config, not a probe result;
    the local panel's one handle on where the fleet board lives."""

    endpoint: str
    reachable: bool
    last_contact_at: datetime | None
    buffer_depth: int


@dataclass(frozen=True)
class RunnerStatusSummary:
    """Identity, pause state, capacities, hub connectivity, and last tick — ``GET /runner``."""

    runner_id: str
    workspace_id: str
    pause: PauseState
    capacities: Capacities
    hub: HubConnectivity
    last_tick_at: datetime | None


@dataclass(frozen=True)
class EnvironmentSlot:
    """One environment in the runner's configured pool (issue #106). Every pool
    environment surfaces, held or not: ``chunk_id``/``held_since`` are set only while the
    environment is bound, ``None`` otherwise — never an invented ref for an idle slot."""

    environment_id: str
    chunk_id: str | None
    held_since: datetime | None


@dataclass(frozen=True)
class EscalationView:
    """One parked escalation with its literal, ready-to-paste resume command. The
    session's own configuration rides beside it — its declared pool and the model/effort
    it ran under (issue #144). All three are ``None`` for a session on the bare
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


@dataclass(frozen=True)
class OpenTakeoverView:
    """One open operator takeover (issue #51, recovery for #52) — the recovery surface
    for a takeover a stranded client left open with no other way to find its
    ``takeover_id``."""

    chunk_id: str
    takeover_id: str
    held_since: datetime


class RunnerStatusService:
    """Composition-root-wired: the store, clock, harness, and this runner's own
    identity/config — everything ``blizzard runner status`` renders (issue #51)."""

    def __init__(
        self,
        store: IReadRunnerStore,
        clock: IClock,
        harness: IHarnessAdapter,
        *,
        runner_id: str,
        workspace_id: str,
        max_agents: int,
        hub_url: str,
        env_pool: tuple[str, ...],
        contact_staleness: timedelta = HUB_CONTACT_STALENESS_THRESHOLD,
    ) -> None:
        self._store = store
        self._clock = clock
        self._harness = harness
        self._runner_id = runner_id
        self._workspace_id = workspace_id
        self._max_agents = max_agents
        self._hub_url = hub_url
        self._env_pool = env_pool
        self._contact_staleness = contact_staleness

    def summary(self) -> RunnerStatusSummary:
        local_paused = self._store.local_paused(self._runner_id)
        hub_paused = self._store.hub_paused(self._runner_id)
        used = len(self._store.list_active_leases())
        contact_at = self._store.hub_contact_at(self._runner_id)
        reachable = contact_at is not None and (self._clock.now() - contact_at) <= self._contact_staleness
        return RunnerStatusSummary(
            runner_id=self._runner_id,
            workspace_id=self._workspace_id,
            pause=PauseState(local=local_paused, hub=hub_paused, effective=local_paused or hub_paused),
            capacities=Capacities(max_agents=self._max_agents, used=used, free=max(self._max_agents - used, 0)),
            hub=HubConnectivity(
                endpoint=self._hub_url,
                reachable=reachable,
                last_contact_at=contact_at,
                buffer_depth=len(self._store.pending_outbound()),
            ),
            last_tick_at=self._store.last_daemon_liveness(),
        )

    def environments(self) -> list[EnvironmentSlot]:
        """The full configured pool (issue #106), joined against the held binding facts.
        A bound environment never silently vanishes: a binding whose id has fallen out of
        the pool still surfaces, and — since ``env_bindings`` has no unique constraint on
        ``environment_id`` — so does every extra binding past the first on one id."""
        held_by_env: dict[str, list[EnvBindingRecord]] = {}
        for binding in self._store.held_bindings():
            held_by_env.setdefault(binding.environment_id, []).append(binding)
        slots = []
        for env_id in self._env_pool:
            bindings = held_by_env.get(env_id, [])
            primary = bindings[0] if bindings else None
            slots.append(
                EnvironmentSlot(
                    environment_id=env_id,
                    chunk_id=primary.chunk_id if primary else None,
                    held_since=primary.bound_at if primary else None,
                )
            )
            for extra in bindings[1:]:
                slots.append(
                    EnvironmentSlot(
                        environment_id=extra.environment_id,
                        chunk_id=extra.chunk_id,
                        held_since=extra.bound_at,
                    )
                )
        pool = set(self._env_pool)
        for env_id, bindings in held_by_env.items():
            if env_id not in pool:
                for binding in bindings:
                    slots.append(
                        EnvironmentSlot(
                            environment_id=binding.environment_id,
                            chunk_id=binding.chunk_id,
                            held_since=binding.bound_at,
                        )
                    )
        return slots

    def open_asks(self) -> list[AskRecord]:
        return self._store.open_asks()

    def recent_facts(self, limit: int) -> list[OutboundFactRecord]:
        """The newest hub-bound facts, acked or not — the local panel's fact log."""
        return self._store.recent_outbound(limit)

    def open_takeovers(self) -> list[OpenTakeoverView]:
        return [
            OpenTakeoverView(chunk_id=t.chunk_id, takeover_id=t.takeover_id, held_since=t.opened_at)
            for t in self._store.open_takeovers()
        ]

    def escalations(self) -> list[EscalationView]:
        views = []
        for escalation in self._store.open_escalations():
            resume_command = ""
            if escalation.session_id is not None:
                bindings = self._store.bindings_for_chunk(escalation.chunk_id)
                if bindings:
                    # Composed from the escalation's own stamps (issue #144), not a fresh
                    # resolution: the operator lands in the configuration it ran with.
                    resume_command = self._harness.resume_command(
                        bindings[0].workdir,
                        escalation.session_id,
                        model=escalation.resolved_model,
                        effort=escalation.resolved_effort,
                    )
            views.append(
                EscalationView(
                    chunk_id=escalation.chunk_id,
                    lease_id=escalation.lease_id,
                    node_id=escalation.node_id,
                    epoch=escalation.epoch,
                    closed_at=escalation.closed_at,
                    resume_command=resume_command,
                    session_name=escalation.session_name,
                    model=escalation.resolved_model,
                    effort=escalation.resolved_effort,
                )
            )
        return views
