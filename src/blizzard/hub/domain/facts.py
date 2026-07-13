"""Runner-reported fact intake (D-044/D-069) — lease mints and escalations.

The runner mints facts locally and reports the fleet-visible ones up to the hub
(``design/domain/events.md``): ``lease.minted`` (every node-step attempt, D-035/D-044)
and ``escalation.recorded`` (retries exhausted, D-009). This service is the domain
rule behind those intake routes — it holds the **write** chunk repository
(``bzh:controller-read-only``) and stamps the landing time from the injected clock
(``bzh:injected-clock``); the routes stay read-only over the store and delegate here.

Why the hub needs the lease mints: the epoch fence (D-007) checks a completion's
epoch against the chunk's **latest** lease epoch. A chunk that visits more than one
runner node (build -> review) mints a fresh epoch per node-step (D-035), so without
the runner reporting each mint the hub's latest would stall at the claim's epoch and
reject the second node's completion as stale. Reporting the mint keeps the two in
lockstep, and it is also what **closes an escalation by supersession** (D-067): a
requeue's fresh lease mint, landing after the escalation, flips ``needs_human`` off
with no resolution fact.
"""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.work import IWriteChunkRepository


class RunnerFactsService:
    """Land runner-reported ``lease.minted`` / ``escalation.recorded`` facts (D-044/D-069)."""

    def __init__(self, *, chunks: IWriteChunkRepository, clock: IClock) -> None:
        self._chunks = chunks
        self._clock = clock

    def record_lease_minted(self, chunk_id: str, *, epoch: int, runner_id: str) -> None:
        """Land a runner's ``lease.minted`` — advances the fence's latest epoch (D-044)."""
        self._chunks.record_lease(chunk_id, epoch=epoch, runner_id=runner_id, at=self._clock.now())

    def record_escalation(self, chunk_id: str, *, epoch: int, takeover_command: str) -> None:
        """Land a runner's ``escalation.recorded`` — the chunk derives ``needs_human`` (D-009)."""
        self._chunks.record_escalation(chunk_id, epoch=epoch, takeover_command=takeover_command, at=self._clock.now())
