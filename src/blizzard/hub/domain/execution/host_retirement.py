"""Host retirement — revoke a host's credential and retire every runner under it."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.execution.fleet import FleetService, RetireInterrupted, RetireOutcome
from blizzard.hub.domain.runners.host import Host, IWriteHostRegistry
from blizzard.hub.domain.runners.lifecycle_version import HostLifecycleVersion
from blizzard.hub.domain.runners.registration import IReadRunnerRegistry, RunnerHoldsRoutes, RunnerLifecycleConflict

_log = get_logger("blizzard.hub.host_retirement")


@domain_model
@dataclass(frozen=True)
class HostRetireOutcome:
    """What one host retire wrote: ``fact_id`` is ``None`` on a re-run over an already-retired host,
    ``revocation_id`` ``None`` when no token was enrolled, and ``runners`` is each runner's own retire outcome."""

    fact_id: int | None
    revocation_id: int | None
    runners: dict[str, RetireOutcome]


class HostRetirementInterrupted(Exception):
    """A runner's retire refused after the host's own retirement committed: it gained a live route after the host's
    refusal check, so its non-forced retire refused, or its lifecycle moved after the roster was read. ``committed`` is
    what did land: the host's fact and revocation, each runner retired before the refusal, and whatever the refusing
    runner's own retire committed before it refused."""

    def __init__(
        self,
        committed: HostRetireOutcome,
        refusal: RunnerHoldsRoutes | RunnerLifecycleConflict | RetireInterrupted,
    ) -> None:
        super().__init__(str(refusal))
        self.committed = committed


class HostRetirementService:
    """Retire a host: its fact and token revocation first, then each runner under it through :class:`FleetService`."""

    def __init__(
        self, *, hosts: IWriteHostRegistry, runners: IReadRunnerRegistry, fleet: FleetService, clock: IClock
    ) -> None:
        self._hosts = hosts
        self._runners = runners
        self._fleet = fleet
        self._clock = clock

    def retire(self, host: Host, *, by: str, force: bool) -> HostRetireOutcome:
        """The host decides the refusal (:meth:`Host.retire`) over every live runner's holdings before any write.
        The host's fact and token revocation then land together, on the lifecycle version the host was read at —
        :class:`~blizzard.hub.domain.runners.host.HostLifecycleConflict` otherwise — so the host can add no runner
        from that instant. Each runner under it as that write left it is then retired with the same ``force``: from
        that instant no write adds a live runner under the host or releases an unretired one, so the roster read after
        it holds every runner the retire still has to retire, and none moved to another host before the fact landed.
        Each runner's retire acts only on the lifecycle version the roster read it at, so one released from the host
        and reinstated before its turn refuses (:class:`HostRetirementInterrupted`) rather than losing its fresh
        token or routes. A crash between the two leaves runners under a retired host unretired; a re-run writes no
        second host fact and re-runs each runner's retire, which finishes it."""
        now = self._clock.now()
        under = self._runners.list_runners(include_retired=True, host_id=host.host_id)
        holdings = {r.runner_id: self._fleet.holdings(r.runner_id) for r in under if not r.retired}
        fact = host.retire(holdings, force=force, by=by, at=now)
        fact_id, revocation_id = self._hosts.record_retirement(
            HostLifecycleVersion(host.host_id, host.lifecycle_version), fact=fact, at=now, by=by
        )
        roster = self._runners.list_runners(include_retired=True, host_id=host.host_id)
        outcomes: dict[str, RetireOutcome] = {}
        for runner in roster:
            try:
                outcomes[runner.runner_id] = self._fleet.retire(runner, by=by, force=force)
            except (RunnerHoldsRoutes, RunnerLifecycleConflict, RetireInterrupted) as exc:
                if isinstance(exc, RetireInterrupted):
                    outcomes[runner.runner_id] = exc.committed
                committed = HostRetireOutcome(fact_id=fact_id, revocation_id=revocation_id, runners=outcomes)
                raise HostRetirementInterrupted(committed, exc) from exc
        _log.info(
            "host retired",
            host_id=host.host_id,
            by=by,
            force=force,
            rerun=fact_id is None,
            runners=sorted(outcomes),
        )
        return HostRetireOutcome(fact_id=fact_id, revocation_id=revocation_id, runners=outcomes)
