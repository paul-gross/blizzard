"""Host retirement — revoke a host's credential and retire every runner under it."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.execution.fleet import FleetService, RetireOutcome
from blizzard.hub.domain.runners.host import Host, IWriteHostRegistry
from blizzard.hub.domain.runners.registration import IReadRunnerRegistry, RunnerHoldsRoutes

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
    """A runner under the host gained a live route after the host's refusal check, so its non-forced retire refused
    after the host's own retirement committed. ``committed`` is what did land: the host's fact and revocation, and
    each runner retired before the refusal."""

    def __init__(self, committed: HostRetireOutcome, refusal: RunnerHoldsRoutes) -> None:
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
        The host's fact and token revocation then land together, so the host can add no runner from that instant, and
        each runner under it is retired with the same ``force``. A crash between the two leaves runners under a
        retired host unretired; a re-run writes no second host fact and re-runs each runner's retire, which finishes
        it."""
        now = self._clock.now()
        under = self._runners.list_runners(include_retired=True, host_id=host.host_id)
        holdings = {r.runner_id: self._fleet.holdings(r.runner_id) for r in under if not r.retired}
        fact = host.retire(holdings, force=force, by=by, at=now)
        fact_id, revocation_id = self._hosts.record_retirement(host.host_id, fact=fact, at=now, by=by)
        outcomes: dict[str, RetireOutcome] = {}
        for runner in under:
            try:
                outcomes[runner.runner_id] = self._fleet.retire(runner, by=by, force=force)
            except RunnerHoldsRoutes as exc:
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
