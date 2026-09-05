"""The reconciliation steps — REAP → PULL → FILL → ADVANCE (``bzh:steppable-loop``).

Each is an individually runnable :class:`Step` over a :class:`LoopContext`. Every step is
idempotent and holds no state of its own — all facts live in the runner store, so a crash
mid-tick and a restart re-run the tick harmlessly; startup recovery is REAP running first.
"""

from __future__ import annotations

import json
from collections.abc import Container, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta

from blizzard.foundation.chunk_status import TERMINAL_STATUSES, ChunkStatus
from blizzard.foundation.crash import crashpoint
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.domain.leases import LeaseRecord, Liveness, as_utc
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.loop.attempt import (
    REAPED,
    Attempt,
)
from blizzard.runner.loop.claim import InterruptedClaims, ReadyQueue
from blizzard.runner.loop.context import LoopContext, ResolvedSubscription
from blizzard.runner.loop.dormant import DormantSession
from blizzard.runner.loop.drain import OutboundDrain
from blizzard.runner.loop.held_chunk import HeldChunk
from blizzard.runner.loop.hub import ChunkNotFoundError, HubClientError
from blizzard.runner.loop.judgement import Judgement
from blizzard.runner.loop.process import IProcessProbe
from blizzard.runner.stores import RunnerStores
from blizzard.runner.subscriptions.subscription_sampler import ExternalSubscriptionUsageSnapshot
from blizzard.wire.chunk import ChunkDetail
from blizzard.wire.facts import (
    EVENT_RECORDED,
    EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED,
    RUNNER_LOCALLY_PAUSED,
)

#: This module's public API — the loop steps it owns, in tick order.
__all__ = [
    "Advance",
    "ContextSample",
    "ExternalUsageSample",
    "Fill",
    "Pull",
    "Reap",
    "Resume",
    "ResumeIntents",
    "SpendCeiling",
    "Step",
]

_log = get_logger("blizzard.runner.loop")

# The env count a chunk gets when nothing says otherwise — a default, not a structural
# assumption; a chunk holding several is representable.
_DEFAULT_ENV_COUNT = 1

# Crash points (``bzh:crash-point-registry``): armed, each SIGKILLs the tick subprocess at
# the boundary it guards; unarmed, a module-global string compare and a no-op.

# REAP — startup recovery runs this first, so these bracket the recovery pass itself.
_CP_REAP_BEFORE = crashpoint("reap.before-expire", "entered REAP; no lease expired yet")
_CP_REAP_AFTER = crashpoint("reap.after-expire", "REAP done; stale leases expired")

# RESUME — the restart re-attach. Its un-recordable middle (a resume whose pid is not yet
# durable) is SPAWN's same by-construction gap; recovery re-runs RESUME idempotently.
_CP_RESUME_BEFORE = crashpoint("resume.before-reattach", "entered RESUME with marked intents; none re-attached yet")

# PULL — the single outbound flusher (store-and-forward drain).
_CP_PULL_BEFORE = crashpoint(
    "pull.before-flush", "entered PULL; registry synced, leases and escalations reconciled, buffer not drained"
)
_CP_PULL_AFTER = crashpoint("pull.after-flush", "PULL done; buffer drained as far as it could")

# The crossing rides `event.recorded`, not a fact kind of its own — both hubs already ingest that
# lane. `(severity, kind)` as `attempt.py` classifies; the kind is the EVENT's, never a fact's.
_CONTEXT_WARNED = ("warning", "worker-context-warned")


@dataclass(frozen=True)
class Step:
    """One reconciliation step over a tick's context — individually runnable, so a test or
    a CLI verb can drive exactly one (``bzh:steppable-loop``)."""

    ctx: LoopContext

    def run(self) -> None:
        raise NotImplementedError


class SpendCeiling(Step):
    """The tick-level kill-switch (issue #61b) — first in the tick."""

    def run(self) -> None:
        """Engage the local pause brake once this runner's rolling-window spend reaches
        ``cost.runner_ceiling_usd``; absent, there is no ceiling (issue #61). Runs **first** in
        the tick so a crossing is visible to every later step in the same pass, engages exactly
        once, and never lifts — only a conscious clear does (tests/test_runner_paused.py)."""
        ctx = self.ctx
        cap = ctx.config.runner_ceiling_usd
        if cap is None:
            return
        if ctx.stores.pause.local_paused(ctx.config.runner_id):
            return  # already engaged — engage-once; `blizzard runner start` is the only clear
        now = ctx.clock.now()
        since = now - timedelta(hours=ctx.config.runner_ceiling_window_hours)
        totals = ctx.stores.usage.usage_since(since)
        if totals.cost_usd < cap:
            return
        partial_note = " (PARTIAL — true spend may be higher)" if totals.cost_partial else ""
        window_hours = ctx.config.runner_ceiling_window_hours
        reason = (
            f"spend ceiling ${cap:.2f} reached over the trailing {window_hours:g}h "
            f"(spend ${totals.cost_usd:.2f}{partial_note})"
        )
        _log.warning(
            f"runner locally paused — {reason}",
            runner_id=ctx.config.runner_id,
            ceiling_usd=cap,
            spend_usd=totals.cost_usd,
            window_hours=ctx.config.runner_ceiling_window_hours,
            cost_partial=totals.cost_partial,
        )
        seq = ctx.stores.pause.record_local_pause(
            ctx.config.runner_id,
            paused=True,
            at=now,
            by="runner-ceiling",
            report_kind=RUNNER_LOCALLY_PAUSED,
            report_payload=json.dumps(
                {"runner_id": ctx.config.runner_id, "by": "runner-ceiling", "at": iso_utc(now), "reason": reason}
            ),
        )
        if ctx.events is not None:
            ctx.events.publish_fact_changed(seq=seq, kind=RUNNER_LOCALLY_PAUSED, chunk_id=None, lease_id=None)


class Reap(Step):
    def run(self) -> None:
        """Expire leases whose worker is gone or **stalled** — each a failed attempt.

        An **orphan** (minted but never spawned) and a **stalled-but-alive** worker end here.
        An exited session-bearing worker is ADVANCE's: exit is the done declaration, and the
        conservative staleness threshold is what keeps the two apart."""
        ctx = self.ctx
        _CP_REAP_BEFORE.reached()
        local_paused = ctx.stores.pause.local_paused(ctx.config.runner_id)
        now = ctx.clock.now()
        parked = ctx.stores.asks.parked_lease_ids()
        taken_over = ctx.stores.takeover.open_takeover_chunk_ids()
        deferred = 0
        for lease in ctx.stores.lease_record.list_active_leases():
            if lease.chunk_id in taken_over:
                continue  # the human holds this session — no loop step touches it
            if lease.lease_id in parked:
                # Dormant on a question: no live worker to stall, so the reap clock is
                # stopped — a parked chunk is never reaped for inactivity.
                continue
            if lease.pid is None or lease.session_id is None:
                _log.info("reaping unspawned lease", lease_id=lease.lease_id, chunk_id=lease.chunk_id)
                Attempt(ctx, lease).fail(reason=REAPED, via="reap")
                continue
            if not ctx.process.is_alive(lease.pid, lease.process_start_time or ""):
                continue  # exited — ADVANCE's (exit-is-done)
            if Liveness.of(ctx.stores.liveness, lease).stale(now):
                if local_paused:
                    # Do not kill a live worker while the brake is on — a pause is not a
                    # drain. The first tick after it clears reaps this lease as it would now.
                    deferred += 1
                    continue
                _log.info("reaping stalled worker", lease_id=lease.lease_id, chunk_id=lease.chunk_id, pid=lease.pid)
                Attempt(ctx, lease).fail(reason=REAPED, via="reap")
            # A live, beating worker runs on.
        if deferred:
            _log.info("reap deferred — locally paused", runner_id=ctx.config.runner_id, count=deferred)
        _CP_REAP_AFTER.reached()


@dataclass(frozen=True)
class ResumeIntents:
    """Marking in-flight leases for same-lease restart-resume — the graceful-shutdown hook
    (#12) and the startup crash-orphan scan (#13). Store-only: no context, no hub.

    Spans leases, asks (parked) and outbound (pending submission), so it holds the
    :class:`~blizzard.runner.stores.RunnerStores` bundle (D4)."""

    stores: RunnerStores

    def mark_graceful(self, *, now: datetime) -> int:
        """Mark every in-flight lease, and return the count. One durable row per mark, so a
        crash mid-marking degrades to the ungraceful path."""
        marked = self._mark(self._resumable(), now=now)
        if marked:
            _log.info("marked in-flight leases for restart-resume", count=marked)
        return marked

    def mark_crashed(self, *, process: IProcessProbe, now: datetime) -> int:
        """Mark the leases a crash orphaned mid-session, and return the count.

        Staleness is measured against :meth:`last_daemon_liveness`, not the clock at recovery,
        which at startup is ``downtime + idle-at-crash``."""
        ended = self.stores.session.session_ended_lease_ids()
        # as_utc: this instant is about to be subtracted from, and a naive one compares wrong.
        last_alive = self.stores.pause.last_daemon_liveness()
        crashed_at = as_utc(last_alive) if last_alive is not None else now
        marked = self._mark(self._crash_orphaned(ended, process, crashed_at), now=now)
        if marked:
            _log.info("marked crash-interrupted leases for restart-resume", count=marked)
        return marked

    def _resumable(self) -> Iterator[LeaseRecord]:
        """Active, session-bearing leases that are neither parked, mid-submission, nor
        mid-elicitation — an unspawned one is REAP's residue, with nothing to resume.

        The elicitation exclusion (D6, review F1/F2/F5) matters on both callers: a graceful
        restart-resume would otherwise wake a second process on the same session, and an
        ungraceful crash-orphan scan would otherwise leave the pre-resume elicitation's stale
        record to be misread as the resumed generation's own verdict — neither path may
        re-mint or resume a lease whose elicitation is in flight."""
        parked = self.stores.asks.parked_lease_ids()
        pending = self.stores.outbound.pending_submission_lease_ids()
        eliciting = self.stores.elicitations.in_flight_elicitation_lease_ids()
        for lease in self.stores.lease_record.list_active_leases():
            if lease.pid is None or lease.session_id is None:
                continue
            if lease.lease_id in parked or lease.lease_id in pending or lease.lease_id in eliciting:
                continue
            yield lease

    def _crash_orphaned(
        self, ended: Container[str], process: IProcessProbe, crashed_at: datetime
    ) -> Iterator[LeaseRecord]:
        for lease in self._resumable():
            if lease.lease_id in ended:
                continue  # declared done (SessionEnd fired) — ADVANCE judges it (exit-is-done)
            if lease.pid is not None and process.is_alive(lease.pid, lease.process_start_time or ""):
                continue  # orphaned-but-alive — REAP re-adopts it, or expires it if the beat went stale
            if Liveness.of(self.stores.liveness, lease).stale(crashed_at):
                # Its process is already gone (the test above), so REAP passes it over and ADVANCE
                # claims it: a verdict elicited from the dead session, a retry consumed only if none is.
                continue
            yield lease

    def _mark(self, leases: Iterator[LeaseRecord], *, now: datetime) -> int:
        marked = 0
        for lease in leases:
            self.stores.resume_intent.record_resume_intent(lease_id=lease.lease_id, marked_at=now)
            marked += 1
        return marked


class Resume(Step):
    def run(self) -> None:
        """Re-attach to in-flight sessions a restart marked for same-lease resume — startup recovery.

        Each marked lease is either resumed in place — unchanged ``lease_id``/``epoch``/
        ``session_id``, no retry consumed — or abandoned with no epoch bump. Runs before ADVANCE so a
        resumed lease reads live again by the time ADVANCE iterates."""
        ctx = self.ctx
        intents = ctx.stores.resume_intent.resume_intent_lease_ids()
        if not intents:
            return
        _CP_RESUME_BEFORE.reached()  # marked intents present; a crash here re-runs RESUME unchanged
        active = {lease.lease_id: lease for lease in ctx.stores.lease_record.list_active_leases()}
        for lease_id in intents:
            lease = active.get(lease_id)
            if lease is None:
                ctx.stores.resume_intent.record_resume_clear(lease_id=lease_id, cleared_at=ctx.clock.now())
                continue
            DormantSession(ctx, lease).restart_or_release()


@dataclass(frozen=True)
class Fenced:
    """Whether the hub has fenced an active lease out from under the worker still on it (#370).

    The signal is the fence itself: an epoch above the lease's, or a restart AT it. The id set
    is the one place a higher one is somebody else's business — a takeover a person is in."""

    taken_over: Container[str]

    def out(self, detail: ChunkDetail, lease: LeaseRecord) -> bool:
        if lease.chunk_id in self.taken_over:
            return False
        if detail.latest_epoch is not None and detail.latest_epoch > lease.epoch:
            return True
        # A restart mints one above the newest epoch THE HUB knows, which excludes a lease whose
        # `lease.minted` is still buffered here — so it can land LEVEL with what it displaces.
        return any(restart.epoch >= lease.epoch for restart in detail.restarts)


class Pull(Step):
    def run(self) -> None:
        """Exchange facts with the hub: sync the registry, reconcile ownership, drain the buffer.

        Reconciliation runs BEFORE the drain, so a preempted lease's queued submission still
        reaches the hub and the drain absorbs the stale-epoch rejection against a lease already
        closed — the retry budget the move must not spend is never reached (#370)."""
        self._sync_registry()
        self._reconcile_leases()
        self._reconcile_escalations()
        self._reconcile_takeovers()
        _CP_PULL_BEFORE.reached()
        OutboundDrain(self.ctx).run()
        _CP_PULL_AFTER.reached()

    def _sync_registry(self) -> None:
        """Register + heartbeat and mirror the hub's pause brake locally.

        Registration is idempotent and doubles as the runner-level liveness heartbeat. The pause
        brake is mirrored locally, and an unreachable hub leaves the last mirrored value standing.
        """
        ctx = self.ctx
        try:
            ctx.hub.register_runner(
                ctx.config.runner_id,
                ctx.config.workspace_id,
                env_capacity=ctx.config.env_capacity,
                url=ctx.config.public_url or None,
                redirect_uris=ctx.config.redirect_uris,
            )
            paused = ctx.hub.fetch_runner_paused(ctx.config.runner_id)
        except HubClientError:
            return  # hub unreachable — keep the last-mirrored brake
        ctx.stores.pause.set_hub_paused(ctx.config.runner_id, paused=paused, at=ctx.clock.now())

    def _reconcile_leases(self) -> None:
        """Reconcile every active lease against the hub's view of its chunk — abandon it if the hub
        no longer routes it here, park it if the operator paused it (issue #46), preempt it if a
        restart moved the chunk out from under it (#370). All three share **one** ``get_chunk``
        per lease, and a transport failure reads as none of them. The pause branch keys on the
        pause *fact*, which an ask-park masks."""
        ctx = self.ctx
        pause_parked = ctx.stores.pause.pause_parked_lease_ids()  # hoisted: the park guard, one read per tick
        fenced = Fenced(ctx.stores.takeover.open_takeover_chunk_ids())
        for lease in ctx.stores.lease_record.list_active_leases():
            try:
                detail = ctx.hub.get_chunk(lease.chunk_id)
            except ChunkNotFoundError:
                # Terminal, not retryable (blizzard#9). Ordered before the HubClientError arm
                # because it subclasses it, or the 404 would be swallowed as "hub unreachable".
                Attempt(ctx, lease).abandon(via="pull")
                continue
            except HubClientError:
                continue  # hub unreachable — last-known directive holds; keep working
            if detail.status == ChunkStatus.STOPPED:
                # Honor the terminal fact directly (issue #118), rather than waiting on the
                # route check below to observe the release.
                Attempt(ctx, lease).abandon(via="pull")
            elif detail.route is None or detail.route.runner_id != ctx.config.runner_id:
                Attempt(ctx, lease).abandon(via="pull")
            elif detail.pause is not None:
                # A pause outranks a move: the paused chunk keeps its lease, route and epoch, and
                # the re-entry happens on the tick after the pause lifts.
                if lease.lease_id not in pause_parked:
                    Attempt(ctx, lease).park_paused(via="pull")
            elif fenced.out(detail, lease):
                Attempt(ctx, lease).preempt(via="pull")

    def _reconcile_escalations(self) -> None:
        """Close a local escalation whose chunk the hub ended (#292, #293) — one ``get_chunk`` each.

        An escalated lease is already closed, so ``_reconcile_leases`` above never sees it, and
        the only local supersession is a later lease mint a stopped chunk never gets. The mark
        is what keeps the read hub-free (``bzh:facts-not-status``)."""
        ctx = self.ctx
        for escalation in ctx.stores.escalations.open_escalations():
            try:
                detail = ctx.hub.get_chunk(escalation.chunk_id)
            except HubClientError as exc:
                # Covers ChunkNotFoundError: an unknown chunk is not a resolution.
                _log.debug("escalation left open — hub unreadable", chunk_id=escalation.chunk_id, error=str(exc))
                continue
            if detail.status not in TERMINAL_STATUSES:
                _log.debug("escalation left open", chunk_id=escalation.chunk_id, hub_status=detail.status.value)
                continue
            ctx.stores.escalations.record_escalation_closure(
                chunk_id=escalation.chunk_id, reason=detail.status.value, at=ctx.clock.now()
            )
            if ctx.events is not None:
                ctx.events.publish_escalation_changed(
                    escalation.chunk_id,
                    cause="closed",
                    lease_id=escalation.lease_id,
                )

    def _reconcile_takeovers(self) -> None:
        """Close an open takeover whose chunk the hub has ended (issue #291) — one ``get_chunk``
        each. The takeover fact now authorizes the resumed session's worker verbs (D1), so a
        chunk the hub ends mid-takeover must not leave that authorization standing forever; this
        is the second, no-person-drives closer alongside the CLI's own end-PATCH. The mark is
        what keeps the read hub-free (``bzh:facts-not-status``)."""
        ctx = self.ctx
        for takeover in ctx.stores.takeover.open_takeovers():
            try:
                detail = ctx.hub.get_chunk(takeover.chunk_id)
            except HubClientError as exc:
                # Covers ChunkNotFoundError: an unknown chunk is not a resolution.
                _log.debug("takeover left open — hub unreadable", chunk_id=takeover.chunk_id, error=str(exc))
                continue
            if detail.status not in TERMINAL_STATUSES:
                _log.debug("takeover left open", chunk_id=takeover.chunk_id, hub_status=detail.status.value)
                continue
            ctx.stores.takeover.record_takeover_end(takeover_id=takeover.takeover_id, ended_at=ctx.clock.now())
            if ctx.events is not None:
                ctx.events.publish_takeover_changed(takeover.chunk_id, takeover.takeover_id, cause="closed")


class Fill(Step):
    def run(self) -> None:
        """Keep the fleet busy: peek → acquire → claim-by-route → bind → spawn.

        Open slots are ``MAX_AGENTS - active_leases``; for each, peek the ready queue, acquire the
        chunk's environments all-or-nothing, and claim the route. Either brake stops *new* claims.
        """
        ctx = self.ctx
        InterruptedClaims(ctx).reconcile()
        hub_paused = ctx.stores.pause.hub_paused(ctx.config.runner_id)
        local_paused = ctx.stores.pause.local_paused(ctx.config.runner_id)
        if hub_paused or local_paused:
            _log.info(
                "paused — no new claims this tick",
                runner_id=ctx.config.runner_id,
                hub_paused=hub_paused,
                local_paused=local_paused,
            )
            return
        slots = ctx.config.max_agents - len(ctx.stores.lease_record.list_active_leases())
        queue = ReadyQueue(ctx)
        for _ in range(max(slots, 0)):
            if not queue.claim_one():
                break


class Advance(Step):
    def run(self) -> None:
        """Judge finished workers and move chunks through the graph.

        Two responsibilities: an exited session-bearing worker is a done declaration to judge and
        buffer, and a held chunk with no active lease is driven separately.
        """
        ctx = self.ctx
        pending = ctx.stores.outbound.pending_submission_lease_ids()
        ask_parked = ctx.stores.asks.ask_parked_lease_ids()
        pause_parked = ctx.stores.pause.pause_parked_lease_ids()
        resume_intents = ctx.stores.resume_intent.resume_intent_lease_ids()
        taken_over = ctx.stores.takeover.open_takeover_chunk_ids()
        for lease in ctx.stores.lease_record.list_active_leases():
            if lease.chunk_id in taken_over:
                continue  # the human holds this session — no loop step touches it
            if lease.pid is None or lease.session_id is None:
                continue  # REAP's residue
            if lease.lease_id in resume_intents:
                continue  # RESUME hasn't re-attached (or abandoned) it yet — not exited work
            if lease.lease_id in pending:
                continue  # outcome elicited, awaiting flush — the node boundary
            if lease.lease_id in pause_parked:
                DormantSession(ctx, lease).on_unpause()  # dormant on an operator pause — resume when it lifts
                continue
            if lease.lease_id in ask_parked:
                DormantSession(ctx, lease).on_answer()  # dormant on a question — resume on the answer
                continue
            if ctx.process.is_alive(lease.pid, lease.process_start_time or ""):
                continue  # worker still running
            self._advance_exited_worker(lease)

        for chunk_id in ctx.stores.environments.live_tenure_chunk_ids():
            if chunk_id in taken_over:
                continue  # the human holds this chunk — no gate/hub-node poll while they do
            if ctx.stores.lease_record.active_lease_for_chunk(chunk_id) is None:
                HeldChunk(ctx, chunk_id).drive()

    def _advance_exited_worker(self, lease: LeaseRecord) -> None:
        """Collect an in-flight elicitation, else park on an open ask, else launch the verdict
        elicitation (blizzard#443).

        The in-flight check runs BEFORE the ask pre-check (D3): once a launch is durable, this
        lease's every later pass is a collect, not a fresh judge — and collecting must not be
        pre-empted by an ask the worker raised *during its live turns, before it exited* (the
        ordinary ask-and-exit shape below). An ask raised *during the elicitation itself* is a
        different case, handled inside `Judgement._judged` after the verdict parse returns
        ``None`` — this pre-check cannot see that one; it is recorded mid-elicitation."""
        if lease.session_id is None:
            return  # not spawned — REAP's residue (guarded by the caller too)
        elicitation = self.ctx.stores.elicitations.in_flight_elicitation(lease.lease_id, lease.epoch)
        if elicitation is not None:
            judgement = Judgement.of(self.ctx, lease)
            if judgement is not None:
                judgement.collect(elicitation)
            return
        # Ask-and-exit: an exit holding an unforwarded ask is a park, an exit with neither is a
        # failure. Not a spawn, so it proceeds regardless of the local brake.
        ask = self.ctx.stores.asks.unforwarded_ask(lease.lease_id)
        if ask is not None:
            DormantSession(self.ctx, lease).park_on_ask(ask)
            return
        judgement = Judgement.of(self.ctx, lease)
        if judgement is not None:
            judgement.run()

        # Every other shape keeps its binding and is polled again next tick.


class ContextSample(Step):
    """Every running lease's live session context, sampled and warned on — never enforced.

    A graph's `rotate` bounds are evaluated at SPAWN time, leaving a long invocation's inside
    unobserved — where a runaway session spends. This closes that gap, not the enforcement one."""

    def run(self) -> None:
        """Sample each active lease's context, warning the first time one crosses.

        No configured line means no transcript reads at all, so a runner that never opts in
        pays nothing. Per-lease failures are isolated: one unreadable transcript must not cost
        the other leases their samples."""
        ctx = self.ctx
        warn_tokens = ctx.config.context_warn_tokens
        if warn_tokens is None or ctx.transcripts is None:
            return
        try:
            leases = ctx.stores.lease_record.list_active_leases()
        except Exception as exc:  # this step is not last in the tick — see ExternalUsageSample
            _log.warning("context sample step failed", detail=str(exc))
            return
        for lease in leases:
            try:
                self._sample(lease, warn_tokens)
            except Exception as exc:  # one lease's read must not end the sweep
                _log.warning("context sample failed", lease_id=lease.lease_id, detail=str(exc))

    def _sample(self, lease: LeaseRecord, warn_tokens: int) -> None:
        ctx = self.ctx
        if lease.session_id is None or ctx.transcripts is None:
            return  # a lease whose spawn has not yet minted a session has nothing to read
        state = ctx.stores.usage.context_sample_state(lease.lease_id)
        now = ctx.clock.now()
        if state is not None and now - state.last_sampled_at < timedelta(
            seconds=ctx.config.context_sample_interval_seconds
        ):
            return
        tokens = ctx.transcripts.context_tokens(lease.session_id, spawn_cwd=self._spawn_cwd(lease))
        # Only the FIRST crossing reports: the warning is a state change, not a level, and a
        # lease past the line samples on for the curve without re-reporting every minute.
        crossing = (
            tokens is not None
            and tokens > warn_tokens
            and not (state is not None and (state.max_context_tokens or 0) > warn_tokens)
        )
        seq = ctx.stores.usage.record_context_sample(
            lease_id=lease.lease_id,
            chunk_id=lease.chunk_id,
            session_id=lease.session_id,
            # `None` is *unmeasured*, recorded as an attempt so the cadence anchor still advances
            # — else an unreadable transcript is re-read every tick instead of every interval.
            context_tokens=tokens,
            sampled_at=now,
            report_kind=EVENT_RECORDED if crossing else "",
            report_payload=json.dumps(self._event(lease, tokens, warn_tokens, now)) if crossing else "",
        )
        if seq is not None and ctx.events is not None:
            ctx.events.publish_fact_changed(
                seq=seq,
                kind=EVENT_RECORDED,
                chunk_id=lease.chunk_id,
                lease_id=lease.lease_id,
            )

    @staticmethod
    def _event(lease: LeaseRecord, tokens: int | None, warn_tokens: int, now: datetime) -> dict[str, object]:
        """The ``event.recorded`` payload one crossing surfaces, in the shape the hub ingests."""
        severity, kind = _CONTEXT_WARNED
        return {
            "severity": severity,
            "kind": kind,
            "chunk_id": lease.chunk_id,
            "lease_id": lease.lease_id,
            "node_name": lease.node_name,
            "message": f"session context {tokens} tokens crossed the {warn_tokens} warn line",
            "detail": {
                "session_id": lease.session_id,
                "context_tokens": tokens,
                "warn_tokens": warn_tokens,
                "sampled_at": iso_utc(now),
            },
        }

    def _spawn_cwd(self, lease: LeaseRecord) -> str | None:
        """The lease's worktree, the transcript locator's multi-match tie-break — never its key.

        Resolved exactly as the transcript pump resolves it, so both lanes read the same file."""
        bindings = self.ctx.stores.environments.bindings_for_chunk(lease.chunk_id)
        return SpawnCwd(self.ctx.config.workspace_root, bindings[0].workdir if bindings else None).path


class ExternalUsageSample(Step):
    """Every declared subscription's own rate-limit utilization (issue #218), each on
    its own per-slug cadence — last in the tick."""

    def run(self) -> None:
        """Sample every declared subscription that is due.

        Each declaration's cadence anchor is derived as ``max(sampled_at)`` for its own
        ``slug``, and an attempt row is recorded either way — ``NULL`` payload on a miss.
        One declaration's failure never stops the next one being sampled this same tick."""
        for resolved in self.ctx.subscriptions:
            try:
                self._sample_one(resolved)
            except Exception as exc:  # second line of defense — the sampler contract already promises this
                _log.warning("external subscription usage sample step failed", slug=resolved.slug, detail=str(exc))

    def _sample_one(self, resolved: ResolvedSubscription) -> None:
        ctx = self.ctx
        anchor = ctx.stores.usage.last_external_usage_attempt_at(resolved.slug)
        if anchor is not None:
            elapsed = ctx.clock.now() - anchor
            if elapsed < timedelta(seconds=resolved.sample_interval_seconds):
                return
        if resolved.sampler is None:
            # Declared, but its provider names no known sampler binding — stays declared
            # and unsampled: no attempt row, since there is no sampler to have failed.
            return
        # `None` is the sampler's own best-effort miss — still an attempt worth recording,
        # so this slug's cadence advances and its last-good windows stay untouched.
        snapshot = resolved.sampler.sample()
        if snapshot is None:
            ctx.stores.usage.record_external_usage_attempt(
                slug=resolved.slug, sampled_at=ctx.clock.now(), payload=None, report_kind="", report_payload=""
            )
            return
        payload = json.dumps(self._payload(resolved, snapshot))
        seq = ctx.stores.usage.record_external_usage_attempt(
            slug=resolved.slug,
            sampled_at=ctx.clock.now(),
            payload=payload,
            report_kind=EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED,
            report_payload=payload,
        )
        if seq is not None and ctx.events is not None:
            ctx.events.publish_fact_changed(
                seq=seq,
                kind=EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED,
                chunk_id=None,
                lease_id=None,
            )

    @staticmethod
    def _payload(resolved: ResolvedSubscription, snapshot: ExternalSubscriptionUsageSnapshot) -> dict[str, object]:
        """The stable JSON shape for a sampled snapshot — both this attempt's stored
        ``payload`` and its buffered outbound report use this exact shape. ``slug`` and
        ``name`` (blizzard#436) name the declared subscription and its operator-facing
        label; a reader ignorant of either still parses ``sampled_at``/``windows``."""
        return {
            "slug": resolved.slug,
            "name": resolved.name,
            "sampled_at": iso_utc(snapshot.sampled_at),
            "windows": [
                {
                    "window": w.window,
                    "utilization_pct": w.utilization_pct,
                    "resets_at": iso_utc(w.resets_at),
                    "window_seconds": w.window_seconds,
                }
                for w in snapshot.windows
            ],
        }
