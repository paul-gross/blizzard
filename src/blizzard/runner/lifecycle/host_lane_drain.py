"""The host's lane drain — the facts the host buffered on its own lane, pushed to its hub under its own token.

A lane (``bzh:lane-contract``) whose only sink is the hub, run as a step of the host tick: bounded per pass, it
pushes the oldest buffered facts in seq order and records the mark the hub answers, which clears every fact at or
below it. The hub de-duplicates against the host's own mark, so a push replayed after a lost answer is applied
once. It drains only while the host's latest report was answered with the host's own identity: against a hub that
does not hold the host, from before hosts, or answering as another host, the facts wait in the buffer and never
ride a runner's lane. A failed pass is retried on the next, with no backoff and no latch."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.foundation.logging import get_logger
from blizzard.runner.hub.client import HostRefused, HubClientError, IHostFactPusher, PushedFact
from blizzard.runner.hub.host_registration import ICurrentHostRegistration
from blizzard.runner.usage.repository import IWriteHostLaneMarkRepository

_log = get_logger("blizzard.runner.host")

#: The facts one pass pushes at most; a larger backlog drains over several host ticks.
HOST_LANE_DRAIN_LIMIT = 100

# Push -> answered mark. A crash here leaves the pushed facts buffered; the next pass re-pushes them and the hub
# answers them already applied.
_CP_AFTER_PUSH = crashpoint("hostlane.after-push.before-mark", "hub applied the host's facts; its mark not recorded")


@dataclass(frozen=True)
class HostLaneDrain:
    """Push the host's buffered facts while the host is registered, and clear what the hub answered."""

    client: IHostFactPusher
    lane: IWriteHostLaneMarkRepository
    registration: ICurrentHostRegistration
    clock: IClock

    def run(self) -> None:
        # A raise must not reach the host tick's other steps.
        try:
            self._run_unsafe()
        except Exception:
            _log.exception("host lane drain failed — continuing the host tick")

    def _run_unsafe(self) -> None:
        if not self.registration.current().drains():
            return
        facts = self.lane.pending_host_facts(limit=HOST_LANE_DRAIN_LIMIT)
        if not facts:
            return
        pushed = [PushedFact(seq=fact.seq, kind=fact.kind, payload_text=fact.payload) for fact in facts]
        try:
            ack = self.client.push_facts(pushed)
        except HubClientError:
            return  # the hub did not answer — the facts stay buffered for the next pass
        if isinstance(ack, HostRefused):
            _log.warning("hub refused the host's lane push", reason=str(ack.reason), detail=ack.detail)
            return
        _CP_AFTER_PUSH.reached()
        for seq in ack.rejected:
            # A contract rejection is not idempotency: surface it, and clear it rather than wedge the lane.
            _log.error("hub rejected a buffered host fact", seq=seq)
        mark = max([ack.high_water, *ack.rejected])
        self.lane.record_host_high_water(seq=mark, at=self.clock.now())
