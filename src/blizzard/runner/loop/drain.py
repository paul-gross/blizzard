"""Draining the outbound buffer: one fact at a time, in order, until one will not deliver."""

from __future__ import annotations

import json
from dataclasses import dataclass

from blizzard.foundation.crash import crashpoint
from blizzard.foundation.logging import get_logger
from blizzard.runner.loop.attempt import FAILED, PARKED, TRANSITIONED, Attempt
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.held_chunk import HeldChunk
from blizzard.runner.loop.hub import HubClientError
from blizzard.runner.loop.outbound import COMPLETION_KIND, DECISION_KIND
from blizzard.runner.store.repository import BufferedFact, LeaseRecord
from blizzard.wire.completion import CompletionSubmission
from blizzard.wire.decision import DecisionSubmission
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse
from blizzard.wire.facts import RunnerFact, RunnerFactBatch

_log = get_logger("blizzard.runner.loop")

# Submit -> ack -> apply-response. The after-submit.before-ack window is the lost-ack replay
# the hub's idempotency must absorb.
_CP_BEFORE_SUBMIT = crashpoint("flush.before-submit", "completion at head of buffer; not submitted")
_CP_AFTER_SUBMIT = crashpoint("flush.after-submit.before-ack", "hub applied the completion; ack not recorded")
_CP_AFTER_ACK = crashpoint("flush.after-ack.before-apply-response", "ack recorded; apply-response not consumed")
_CP_AFTER_APPLY = crashpoint("flush.after-apply-response", "apply-response consumed; chunk continued in place")

# The between-attempts boundary the per-chunk spend cap checks at (issue #61a): a crash here
# leaves no active lease and no escalation, recovered by FILL's interrupted-claim reconcile.
_CP_AFTER_CLOSURE = crashpoint(
    "advance.after-closure.before-cost-cap-check", "attempt closed; cap check and next-step decision not yet made"
)


@dataclass(frozen=True)
class OutboundDrain:
    """The single flusher for this runner's store-and-forward buffer."""

    ctx: LoopContext

    def run(self) -> None:
        for fact in self.ctx.store.pending_outbound():
            if not self._deliver(fact):
                break  # transport failure — stop; retry the backlog next tick

    def _deliver(self, fact: BufferedFact) -> bool:
        if fact.kind == COMPLETION_KIND:
            return self._completion(fact)
        if fact.kind == DECISION_KIND:
            return self._decision(fact)
        return self._event(fact)

    def _event(self, fact: BufferedFact) -> bool:
        """Push one buffered fact to POST /events — the generic arm."""
        batch = RunnerFactBatch(
            runner_id=self.ctx.config.runner_id,
            facts=[RunnerFact(seq=fact.seq, kind=fact.kind, payload=json.loads(fact.payload))],
        )
        try:
            ack = self.ctx.hub.push_facts(batch)
        except HubClientError:
            return False  # hub unreachable — the fact stays buffered, retried next tick
        if fact.seq in ack.rejected:
            # A contract rejection is not idempotency — surface it, but do not wedge the FIFO
            # drain on a fact the hub will never accept: ack and move on.
            _log.error("hub rejected buffered fact", seq=fact.seq, kind=fact.kind)
        self._ack(fact)
        return True

    def _completion(self, fact: BufferedFact) -> bool:
        """Submit a buffered completion and drive its apply-response.

        Idempotent by construction: the apply is epoch-idempotent, and the response is acted on
        only while the lease is still active, so a re-flush past a lost ack just clears the
        buffer."""
        submission = CompletionSubmission.model_validate(json.loads(fact.payload)["submission"])
        _CP_BEFORE_SUBMIT.reached()
        try:
            response = self.ctx.hub.submit_completion(fact.chunk_id or "", submission)
        except HubClientError:
            return False  # stays durable in the buffer; the mid-node worker is unaffected
        _CP_AFTER_SUBMIT.reached()  # hub applied it; a crash here is the lost-ack replay
        self._ack(fact)
        _CP_AFTER_ACK.reached()
        lease = self.ctx.store.active_lease(fact.lease_id or "")
        if lease is None:
            return True  # already advanced on an earlier flush whose ack was lost
        self._consume(lease, response)
        _CP_AFTER_APPLY.reached()
        return True

    def _decision(self, fact: BufferedFact) -> bool:
        """Submit a buffered runner-config gate decision and park the chunk.

        There is no next envelope to continue into, so the flush closes the lease and holds the
        environments. The apply is natural-key idempotent, so a re-flush just clears the buffer."""
        submission = DecisionSubmission.model_validate(json.loads(fact.payload)["submission"])
        try:
            response = self.ctx.hub.submit_decision(fact.chunk_id or "", submission)
        except HubClientError:
            return False  # decision stays durable in the buffer; retried next tick
        self._ack(fact)
        lease = self.ctx.store.active_lease(fact.lease_id or "")
        if lease is None:
            return True  # already parked on an earlier flush whose ack was lost
        if response.outcome == ApplyOutcome.FAILURE:
            _log.warning("decision rejected on flush", chunk_id=lease.chunk_id, detail=response.detail or "")
            Attempt(self.ctx, lease).fail(reason=FAILED, via="pull")
            return True
        Attempt(self.ctx, lease).close(PARKED, self.ctx.clock.now())
        _log.info("chunk parked at runner-config gate", chunk_id=lease.chunk_id, node=lease.node_name)
        return True

    def _consume(self, lease: LeaseRecord, response: ApplyResponse) -> None:
        """Record the closure and continue in place per the hub's apply-response.

        Between the closure and any next-attempt spawn sits the boundary the per-chunk spend cap
        checks at: the attempt just closed is genuinely done, so parking here kills nothing live."""
        if response.outcome == ApplyOutcome.FAILURE:
            # A semantic rejection — a stale-epoch or terminal completion. The attempt failed;
            # requeue or escalate. The chunk never advanced.
            _log.warning("completion rejected on flush", chunk_id=lease.chunk_id, detail=response.detail or "")
            Attempt(self.ctx, lease).fail(reason=FAILED, via="pull")
            return
        Attempt(self.ctx, lease).close(TRANSITIONED, self.ctx.clock.now())
        _CP_AFTER_CLOSURE.reached()
        if response.outcome == ApplyOutcome.NEXT and self._capped(lease):
            return  # capped — needs_human; the next attempt is not spawned
        HeldChunk(self.ctx, lease.chunk_id).apply(
            response.outcome, response.next_envelope, self.ctx.store.bindings_for_chunk(lease.chunk_id)
        )

    def _capped(self, lease: LeaseRecord) -> bool:
        """True — chunk parked ``needs_human`` — iff its spend has reached ``cost.chunk_cap_usd``.

        Reads the hub-derived total (``bzh:facts-not-status``), never a local sum. That total is
        a LOWER BOUND — a cost-absent row contributes $0 — so the cap trips conservatively."""
        cap = self.ctx.config.chunk_cap_usd
        if cap is None:
            return False
        try:
            detail = self.ctx.hub.get_chunk(lease.chunk_id)
        except HubClientError:
            return False  # hub unreachable — re-checked at the next step boundary
        cost = detail.cost
        if cost.cost_usd < cap:
            return False
        partial_note = " (PARTIAL — true spend may be higher)" if cost.cost_partial else ""
        _log.warning(
            f"chunk parked — spend cap exceeded{partial_note}",
            chunk_id=lease.chunk_id,
            cap_usd=cap,
            spend_usd=cost.cost_usd,
            cost_partial=cost.cost_partial,
        )
        Attempt(self.ctx, lease).escalate(
            reason=f"spend cap ${cap:.2f} reached (spend ${cost.cost_usd:.2f}{partial_note})"
        )
        return True

    def _ack(self, fact: BufferedFact) -> None:
        self.ctx.store.ack_outbound(fact.seq, acked_at=self.ctx.clock.now())
