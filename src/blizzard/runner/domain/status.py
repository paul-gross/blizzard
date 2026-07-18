"""The runner's machine-local status view (``bzh:domain-core``, issue #51).

The machine-local counterpart to the hub's fleet-wide ``blizzard hub status``:
this runner's own capacities, held environments, open asks, and parked
escalations — everything derived from store facts at read time
(``bzh:facts-not-status``), no new stored status columns. Hub-free by
construction wherever the hub is not the fact's own home: identity, pause
states, capacities, held bindings, and open asks come from the local store
alone. The one exception is hub *reachability* itself, which has no fact of its
own to read — :meth:`RunnerStatusService.summary` derives it from how stale
``hub_contact_at`` (the last successful PULL round trip) reads against ``now``,
so the summary stays truthful with the hub down rather than needing a live call.

Escalation resume commands are **recomputed** here from the escalated lease's
session id and its chunk's still-held binding, via the same
:meth:`~blizzard.runner.harness.adapter.IHarnessAdapter.resume_command` call
``_escalate`` (``runner/loop/steps.py``) used to mint the original — not read back
off the outbound buffer, which only holds the *unacked* tail and would go blank
the moment the fact flushes to the hub.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from blizzard.foundation.clock import IClock
from blizzard.runner.harness.adapter import IHarnessAdapter
from blizzard.runner.store.repository import AskRecord, IReadRunnerStore

__all__ = [
    "HUB_CONTACT_STALENESS_THRESHOLD",
    "Capacities",
    "EscalationView",
    "HeldEnvironment",
    "HubConnectivity",
    "PauseState",
    "RunnerStatusService",
    "RunnerStatusSummary",
]

#: How stale the last successful hub contact (:meth:`IReadRunnerStore.hub_contact_at`)
#: may read before the summary calls the hub unreachable. Deliberately generous —
#: several ticks' worth (``DEFAULT_TICK_SECONDS`` is 30s) — the same conservative-by-design
#: choice :data:`blizzard.runner.domain.leases.HEARTBEAT_STALENESS_THRESHOLD` makes: a
#: single slow tick or a momentary blip must never flip this false.
HUB_CONTACT_STALENESS_THRESHOLD = timedelta(minutes=5)


@dataclass(frozen=True)
class PauseState:
    """The pause brake's two independent surfaces, plus their effective OR.

    Mirrors :class:`~blizzard.runner.api.control.RunnerControlView`'s three-value
    shape — reported apart because they are cleared by different verbs
    (``blizzard runner start`` vs. ``blizzard hub resume``)."""

    local: bool
    hub: bool
    effective: bool


@dataclass(frozen=True)
class Capacities:
    """Agent slots — the same math FILL claims against (``loop/steps.py``'s ``fill``)."""

    max_agents: int
    used: int
    free: int


@dataclass(frozen=True)
class HubConnectivity:
    """Hub reachability, derived from staleness, plus the outbound backlog depth."""

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
class HeldEnvironment:
    """One environment this runner currently holds — ``GET /environments``."""

    environment_id: str
    chunk_id: str
    held_since: datetime


@dataclass(frozen=True)
class EscalationView:
    """One parked escalation with its literal, ready-to-paste resume command."""

    chunk_id: str
    lease_id: str
    node_id: str
    epoch: int
    closed_at: datetime
    resume_command: str


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
        contact_staleness: timedelta = HUB_CONTACT_STALENESS_THRESHOLD,
    ) -> None:
        self._store = store
        self._clock = clock
        self._harness = harness
        self._runner_id = runner_id
        self._workspace_id = workspace_id
        self._max_agents = max_agents
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
                reachable=reachable,
                last_contact_at=contact_at,
                buffer_depth=len(self._store.pending_outbound()),
            ),
            last_tick_at=self._store.last_daemon_liveness(),
        )

    def held_environments(self) -> list[HeldEnvironment]:
        return [
            HeldEnvironment(environment_id=b.environment_id, chunk_id=b.chunk_id, held_since=b.bound_at)
            for b in self._store.held_bindings()
        ]

    def open_asks(self) -> list[AskRecord]:
        return self._store.open_asks()

    def escalations(self) -> list[EscalationView]:
        views = []
        for escalation in self._store.open_escalations():
            resume_command = ""
            if escalation.session_id is not None:
                bindings = self._store.bindings_for_chunk(escalation.chunk_id)
                if bindings:
                    resume_command = self._harness.resume_command(bindings[0].workdir, escalation.session_id)
            views.append(
                EscalationView(
                    chunk_id=escalation.chunk_id,
                    lease_id=escalation.lease_id,
                    node_id=escalation.node_id,
                    epoch=escalation.epoch,
                    closed_at=escalation.closed_at,
                    resume_command=resume_command,
                )
            )
        return views
