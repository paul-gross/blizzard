"""Draining the outbound buffer: contiguous runs of generic-kind facts batched into one
``push_facts`` call each, completions and decisions still delivered one at a time — in
order, until one will not deliver."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from blizzard.foundation.crash import crashpoint
from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.foundation.leases import LeaseClosureReason
from blizzard.foundation.logging import get_logger
from blizzard.foundation.node_steps import ApplyOutcome
from blizzard.foundation.trace_ids import StepKey, step_root
from blizzard.runner.hub.client import HubClientError, PushedFact
from blizzard.runner.hub.outbound import buffered_completion, buffered_gate
from blizzard.runner.hub.outbound_buffer import BufferedFact
from blizzard.runner.leases.model import Lease
from blizzard.runner.lifecycle.attempt import Attempt, AttemptContext
from blizzard.runner.lifecycle.held_chunk import HeldChunk, HeldChunkContext
from blizzard.runner.lifecycle.model import (
    COMPLETION_CLOSURES,
    CompletionMove,
    DecisionMove,
    completion_move,
    decision_move,
    spend_cap_detail,
    spend_cap_partial_note,
    spend_cap_reached,
)
from blizzard.runner.lifecycle.spawn import SpawnConfig
from blizzard.runner.node_steps.chunk_state import ChunkSpend
from blizzard.runner.node_steps.submissions import ApplyReply

_log = get_logger("blizzard.runner.loop")

#: This drain's own per-``run()`` slice bound — a large backlog drains over
#: several ticks rather than one run holding the whole buffer's payload set in memory.
_DRAIN_LIMIT = 100

# Submit -> ack -> apply-response. The after-submit.before-ack window is the lost-ack replay
# the hub's idempotency must absorb.
_CP_BEFORE_SUBMIT = crashpoint("flush.before-submit", "completion at head of buffer; not submitted")
_CP_AFTER_SUBMIT = crashpoint("flush.after-submit.before-ack", "hub applied the completion; ack not recorded")
_CP_AFTER_ACK = crashpoint("flush.after-ack.before-apply-response", "ack recorded; apply-response not consumed")
_CP_AFTER_APPLY = crashpoint("flush.after-apply-response", "apply-response consumed; chunk continued in place")

# The between-attempts boundary the per-chunk spend cap checks at: a crash here
# leaves no active lease and no escalation, recovered by FILL's interrupted-claim reconcile.
_CP_AFTER_CLOSURE = crashpoint(
    "advance.after-closure.before-cost-cap-check", "attempt closed; cap check and next-step decision not yet made"
)


class DrainConfig(SpawnConfig, Protocol):
    @property
    def chunk_cap_usd(self) -> float | None: ...


class DrainContext(AttemptContext, HeldChunkContext, Protocol):
    @property
    def config(self) -> DrainConfig: ...


@dataclass(frozen=True)
class OutboundDrain:
    """The single flusher for this runner's store-and-forward buffer."""

    ctx: DrainContext

    def run(self) -> None:
        # An uncaught raise would escape through `Pull` and skip Fill and Advance.
        try:
            self._run_unsafe()
        except Exception:
            _log.exception("outbound drain failed — continuing the tick", runner_name=self.ctx.config.runner_name)

    def _run_unsafe(self) -> None:
        """Walk this tick's own bounded slice in seq order, batching every contiguous
        run of generic-kind facts into one ``push_facts`` call; a completion or decision
        fact first flushes the run collected so far, then routes to its own arm unchanged."""
        run: list[BufferedFact] = []
        for fact in self.ctx.stores.outbound.pending_outbound(limit=_DRAIN_LIMIT):
            if not fact.is_submission:
                run.append(fact)
                continue
            if run:
                if not self._flush_run(run):
                    return  # transport failure — stop; retry the backlog next tick
                run = []
            handler = self._completion if fact.is_completion else self._decision
            if not handler(fact):
                return  # transport failure — stop; retry the backlog next tick
        if run:
            self._flush_run(run)

    def _flush_run(self, run: list[BufferedFact]) -> bool:
        """Push one contiguous run of generic-kind facts to POST /events in a single
        request, then ack every seq the run carried."""
        pushed = [PushedFact(seq=fact.seq, kind=fact.kind, payload=json.loads(fact.payload)) for fact in run]
        try:
            ack = self.ctx.hub.push_facts(pushed)
        except HubClientError:
            return False  # hub unreachable — the whole run stays buffered, retried next tick
        # Every chunk this run named a fact for, so a later get() this tick sees the push.
        for chunk_id in {fact.chunk_id for fact in run if fact.chunk_id}:
            self.ctx.chunk_views.invalidate(chunk_id)
        for fact in run:
            if fact.seq in ack.rejected:
                # A contract rejection is not idempotency — surface it, but do not wedge the
                # FIFO drain on a fact the hub will never accept: ack and move on.
                if fact.seq in ack.route_ended:
                    _log.warning(
                        "hub rejected buffered fact",
                        reason="chunk route ended",
                        seq=fact.seq,
                        kind=fact.kind,
                        chunk_id=fact.chunk_id,
                    )
                else:
                    _log.error("hub rejected buffered fact", seq=fact.seq, kind=fact.kind)
        self._ack_run(run)
        return True

    def _completion(self, fact: BufferedFact) -> bool:
        """Submit a buffered completion and drive its apply-response.

        Idempotent by construction: the apply is epoch-idempotent, and the response is acted on
        only while the lease is still active, so a re-flush past a lost ack just clears the
        buffer."""
        submission = buffered_completion(fact)
        _CP_BEFORE_SUBMIT.reached()
        try:
            with self.ctx.tracer.under(step_root(StepKey.attempt(fact.chunk_id or "", submission.epoch))):
                response = self.ctx.hub.submit_completion(fact.chunk_id or "", submission)
        except HubClientError:
            return False  # stays durable in the buffer; the mid-node worker is unaffected
        _CP_AFTER_SUBMIT.reached()  # hub applied it; a crash here is the lost-ack replay
        if fact.chunk_id:
            self.ctx.chunk_views.invalidate(fact.chunk_id)  # a later get() this tick sees the apply
        self._ack(fact)
        _CP_AFTER_ACK.reached()
        lease = self.ctx.stores.lease_record.active_lease(fact.lease_id or "")
        if lease is None:
            return True  # already advanced on an earlier flush whose ack was lost
        self._consume(lease, response)
        _CP_AFTER_APPLY.reached()
        return True

    def _decision(self, fact: BufferedFact) -> bool:
        """Submit a buffered runner-config gate decision and park the chunk.

        There is no next envelope to continue into, so the flush closes the lease and holds the
        environments. The apply is natural-key idempotent, so a re-flush just clears the buffer."""
        submission = buffered_gate(fact)
        try:
            with self.ctx.tracer.under(step_root(StepKey.attempt(fact.chunk_id or "", submission.epoch))):
                response = self.ctx.hub.submit_decision(fact.chunk_id or "", submission)
        except HubClientError:
            return False  # decision stays durable in the buffer; retried next tick
        if fact.chunk_id:
            self.ctx.chunk_views.invalidate(fact.chunk_id)  # a later get() this tick sees the apply
        self._ack(fact)
        lease = self.ctx.stores.lease_record.active_lease(fact.lease_id or "")
        if lease is None:
            return True  # already parked on an earlier flush whose ack was lost
        if decision_move(response.outcome) is DecisionMove.FAIL:
            _log.warning("decision rejected on flush", chunk_id=lease.chunk_id, detail=response.detail or "")
            Attempt(self.ctx, lease).fail(reason=LeaseClosureReason.FAILED, via="pull")
            return True
        Attempt(self.ctx, lease).close(LeaseClosureReason.PARKED, self.ctx.clock.now())
        _log.info("chunk parked at runner-config gate", chunk_id=lease.chunk_id, node=lease.node_name)
        return True

    def _consume(self, lease: Lease, response: ApplyReply) -> None:
        """Record the closure and continue in place per the hub's apply-response.

        Between the closure and any next-attempt spawn sits the boundary the per-chunk spend cap
        checks at: the attempt just closed is genuinely done, so parking here kills nothing live."""
        # Only an advancing completion can reach the cap, so only then is the spend read.
        breach = self._spend_cap_breach(lease) if response.outcome == ApplyOutcome.NEXT else None
        move = completion_move(response.outcome, capped=breach is not None)
        if move is CompletionMove.FAIL:
            # A semantic rejection — a stale-epoch or terminal completion. The attempt failed;
            # requeue or escalate. The chunk never advanced.
            _log.warning("completion rejected on flush", chunk_id=lease.chunk_id, detail=response.detail or "")
            Attempt(self.ctx, lease).fail(reason=LeaseClosureReason.FAILED, via="pull")
            return
        if move is CompletionMove.ESCALATE_SPEND_CAP and breach is not None:
            cost, cap = breach
            # Closed escalated, so the escalation reads open here like any other, and the next node is
            # not entered, so no attempt there is spent.
            closure = COMPLETION_CLOSURES[move]
            attempt = Attempt(self.ctx, lease)
            attempt.close(closure.reason, self.ctx.clock.now(), escalation_cause=closure.escalation_cause)
            _CP_AFTER_CLOSURE.reached()
            attempt.escalate(cause=EscalationCause.SPEND_CAP, detail=spend_cap_detail(cost, cap))
            return
        Attempt(self.ctx, lease).close(COMPLETION_CLOSURES[CompletionMove.CLOSE_AND_APPLY].reason, self.ctx.clock.now())
        _CP_AFTER_CLOSURE.reached()
        HeldChunk(self.ctx, lease.chunk_id).apply(
            response.outcome, response.next_envelope, self.ctx.stores.environments.bindings_for_chunk(lease.chunk_id)
        )

    def _spend_cap_breach(self, lease: Lease) -> tuple[ChunkSpend, float] | None:
        """The chunk's spend and the cap it reached, when it has (:func:`spend_cap_reached`).

        Reads the hub-derived total (``bzh:facts-not-status``), never a local sum. That total is
        a LOWER BOUND — a row with no billed cost contributes $0, estimate or not — so the cap
        trips conservatively, and its PARTIAL is the total's ``billed_partial``."""
        cap = self.ctx.config.chunk_cap_usd
        if cap is None:
            return None
        try:
            view = self.ctx.chunk_views.get(lease.chunk_id)
        except HubClientError:
            # Covers ChunkNotFoundError too — re-checked at the next step boundary either way.
            return None
        cost = view.cost
        if not spend_cap_reached(cost, cap):
            return None
        _log.warning(
            f"chunk parked — spend cap exceeded{spend_cap_partial_note(cost)}",
            chunk_id=lease.chunk_id,
            cap_usd=cap,
            spend_usd=cost.cost_usd,
            cost_partial=cost.billed_partial,
        )
        return cost, cap

    def _ack(self, fact: BufferedFact) -> None:
        self.ctx.stores.outbound.ack_outbound(fact.seq, acked_at=self.ctx.clock.now())
        if self.ctx.events is not None:
            # Re-announces the enqueue's own seq — the fact log's `acked_at` marker
            # otherwise stays stale until the next backstop poll; the published event carries no acked state.
            self.ctx.events.publish_fact_changed(
                seq=fact.seq,
                kind=fact.kind,
                chunk_id=fact.chunk_id,
                lease_id=fact.lease_id,
            )

    def _ack_run(self, run: list[BufferedFact]) -> None:
        """One store transaction acks every seq the delivered run carried."""
        acked_at = self.ctx.clock.now()
        self.ctx.stores.outbound.ack_outbound_batch([fact.seq for fact in run], acked_at=acked_at)
        if self.ctx.events is not None:
            for fact in run:
                self.ctx.events.publish_fact_changed(
                    seq=fact.seq,
                    kind=fact.kind,
                    chunk_id=fact.chunk_id,
                    lease_id=fact.lease_id,
                )
