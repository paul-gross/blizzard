"""Runner-reported fact intake (D-044/D-069) — lease mints and escalations.

The runner mints facts locally and reports the fleet-visible ones up to the hub
(``design/domain/events.md``): ``lease.minted`` (every node-step attempt, D-035/D-044)
and ``escalation.recorded`` (retries exhausted, D-009). Two landing points share the
same domain writes:

* :class:`RunnerFactsService` — the direct, single-fact intake behind the typed
  ``POST /chunks/{id}/leases`` and ``/chunks/{id}/escalations`` routes.
* :class:`FactIngestService` — the batched, seq-idempotent store-and-forward push
  behind ``POST /api/events`` (D-069): every fact rides the runner's outbound buffer
  with a per-runner monotonic seq, and a replay (lost ack, outage backlog) is re-acked
  against the hub's per-runner **high-water mark** without re-applying. This is the
  path the reconciliation loop uses.

Both hold the **write** chunk repository (``bzh:controller-read-only``) and stamp the
landing time from the injected clock (``bzh:injected-clock``); the routes stay
read-only over the store and delegate here.

Why the hub needs the lease mints: the epoch fence (D-007) checks a completion's epoch
against the chunk's **latest** lease epoch. A chunk that visits more than one runner
node (build -> review) mints a fresh epoch per node-step (D-035), so without the runner
reporting each mint the hub's latest would stall at the claim's epoch and reject the
second node's completion as stale. Reporting the mint keeps the two in lockstep, and it
is also what **closes an escalation by supersession** (D-067): a requeue's fresh lease
mint, landing after the escalation, flips ``needs_human`` off with no resolution fact.
"""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.hub.domain.work import IWriteChunkRepository
from blizzard.wire.facts import (
    ESCALATION_RECORDED,
    LEASE_MINTED,
    RunnerFactAck,
    RunnerFactBatch,
)

_log = get_logger("blizzard.hub.facts")


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


class FactIngestService:
    """Apply a runner's batched pushed facts idempotently against its high-water mark (D-069)."""

    def __init__(self, *, chunks: IWriteChunkRepository, clock: IClock) -> None:
        self._chunks = chunks
        self._clock = clock

    def ingest(self, batch: RunnerFactBatch) -> RunnerFactAck:
        mark = self._chunks.runner_high_water(batch.runner_id)
        applied: list[int] = []
        already: list[int] = []
        rejected: list[int] = []

        for fact in sorted(batch.facts, key=lambda f: f.seq):
            if fact.seq <= mark:
                already.append(fact.seq)
                continue
            if not self._apply(batch.runner_id, fact.kind, fact.payload):
                # An unknown kind is a contract mismatch, not an idempotency skip: do
                # not advance the mark past it, and name it so the runner surfaces it.
                rejected.append(fact.seq)
                continue
            mark = fact.seq
            applied.append(fact.seq)

        if applied:
            self._chunks.set_runner_high_water(batch.runner_id, seq=mark, at=self._clock.now())
        _log.info(
            "runner facts ingested",
            runner_id=batch.runner_id,
            high_water=mark,
            applied=len(applied),
            already=len(already),
            rejected=len(rejected),
        )
        return RunnerFactAck(
            runner_id=batch.runner_id,
            high_water=mark,
            applied=applied,
            already_applied=already,
            rejected=rejected,
        )

    def _apply(self, runner_id: str, kind: str, payload: dict[str, object]) -> bool:
        now = self._clock.now()
        if kind == LEASE_MINTED:
            self._chunks.record_lease(
                str(payload["chunk_id"]),
                epoch=int(payload["epoch"]),  # type: ignore[arg-type]
                runner_id=runner_id,
                at=now,
            )
            return True
        if kind == ESCALATION_RECORDED:
            self._chunks.record_escalation(
                str(payload["chunk_id"]),
                epoch=int(payload["epoch"]),  # type: ignore[arg-type]
                takeover_command=str(payload.get("takeover_command", "")),
                at=now,
            )
            return True
        _log.warning("unknown runner fact kind", kind=kind)
        return False
