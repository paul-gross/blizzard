"""A host's report — the record it refreshes on every tick, and the change it publishes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.runners.host import Host, HostReport, IWriteHostRegistry
from blizzard.hub.domain.runners.registration import STALE_AFTER

_log = get_logger("blizzard.hub.host_report")


@domain_model
@dataclass(frozen=True)
class Reported:
    """What one report recorded: whether it was the host's first since it was added, the name the host now holds, and
    whether it ``changed`` what the hub held — or found the host offline. A bare liveness refresh is not a change."""

    first: bool
    name: str
    changed: bool


class HostReportService:
    """Record a host's report, which doubles as its heartbeat and so is never refused over its content."""

    def __init__(self, *, hosts: IWriteHostRegistry, clock: IClock, stale_after: timedelta = STALE_AFTER) -> None:
        self._hosts = hosts
        self._clock = clock
        self._stale_after = stale_after

    def report(self, held: Host, reported: HostReport) -> Reported:
        """Record ``reported`` against ``held``, the host its bearer token names. A retired host raises
        :class:`~blizzard.hub.domain.runners.host.HostRetired` before any write."""
        now = self._clock.now()
        decision = held.report(
            reported,
            other_live_names=self._hosts.live_names(excluding=held.host_id),
            now=now,
            threshold=self._stale_after,
        )
        first = self._hosts.record_report(held.host_id, reported, name=decision.name, at=now)
        name = decision.name or held.name
        _log.info("host reported", host_id=held.host_id, name=name, first_time=first, changed=decision.changed or first)
        return Reported(first=first, name=name, changed=decision.changed or first)
