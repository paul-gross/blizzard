"""The reconciliation steps — REAP → PULL → FILL → ADVANCE (``bzh:steppable-loop``).

Each is an individually runnable :class:`Step` over a :class:`LoopContext`. Every step is
idempotent and holds no state of its own — all facts live in the runner store, so a crash
mid-tick and a restart re-run the tick harmlessly; startup recovery is REAP running first.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Container, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import ValidationError

from blizzard.foundation.crash import crashpoint
from blizzard.foundation.event_log import EVENT_LOG_SEVERITY, EventLogKind
from blizzard.foundation.fact_kinds import EVENT_RECORDED
from blizzard.foundation.leases import LeaseClosureReason
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.environments.repository import EnvBinding, group_bindings_by_chunk
from blizzard.runner.harness.registry import UnavailableHarnessError, UnknownHarnessError
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.hub.client import ChunkNotFoundError, HubClientError
from blizzard.runner.leases import Lease, Liveness, as_utc
from blizzard.runner.leases.overload import backing_off_facts
from blizzard.runner.lifecycle.attempt import Attempt
from blizzard.runner.lifecycle.claim import InterruptedClaims, ReadyQueue
from blizzard.runner.lifecycle.dormant import DormantSession
from blizzard.runner.lifecycle.drain import OutboundDrain
from blizzard.runner.lifecycle.held_chunk import HeldChunk
from blizzard.runner.lifecycle.judgement.judgement import Judgement, elicitation_still_pending
from blizzard.runner.lifecycle.model import (
    AdvanceMove,
    Fenced,
    LeaseReconcileMove,
    ReapMove,
    TakeoverHolds,
    advance_move,
    crash_orphaned,
    lease_reconcile_move,
    open_slots,
    reap_move,
    resumable,
)
from blizzard.runner.lifecycle.spawn import Spawner
from blizzard.runner.lifecycle.takeover import TakeoverCloser, TakeoverCloseScope
from blizzard.runner.lifecycle.usage_limit import classify_worker_usage_limit, engage_and_park_worker
from blizzard.runner.loop.context import LoopContext, ResolvedSubscription
from blizzard.runner.process.probe import IProcessProbe
from blizzard.runner.stores import RunnerStores
from blizzard.runner.subscriptions.subscription_sampler import (
    ExternalSubscriptionUsageSnapshot,
    SampleMissReason,
    sample_due,
)
from blizzard.runner.throttle.overload import (
    classify_worker_overload,
    record_worker_overload,
    reset_if_streak_open,
)
from blizzard.runner.throttle.pause import PauseService, spend_ceiling_reason
from blizzard.runner.usage.repository import ContextSampleState, external_usage_attempt
from blizzard.wire.facts import ExternalSubscriptionUsageWindowFact
from blizzard.wire.runner import RunnerSubscriptionDeclaration

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
    "Retention",
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
# lane. `(severity, kind)` as `lifecycle/attempt.py` classifies; the kind is the EVENT's, never a fact's.
_CONTEXT_WARNED: EventLogKind = "worker-context-warned"


@dataclass(frozen=True)
class Step:
    """One reconciliation step over a tick's context — individually runnable, so a test or
    a CLI verb can drive exactly one (``bzh:steppable-loop``)."""

    ctx: LoopContext

    def run(self) -> None:
        raise NotImplementedError


class SpendCeiling(Step):
    """The tick-level kill-switch — first in the tick."""

    def run(self) -> None:
        """Engage the local pause brake once this runner's rolling-window spend reaches
        ``cost.runner_ceiling_usd``; absent, there is no ceiling. Runs **first** in
        the tick so a crossing is visible to every later step in the same pass, engages exactly
        once, and never lifts — only a conscious clear does (tests/test_runner_paused.py)."""
        ctx = self.ctx
        cap = ctx.config.runner_ceiling_usd
        if cap is None:
            return
        if ctx.stores.pause.local_paused(ctx.config.runner_id):
            return  # already engaged — engage-once; `blizzard runner start` is the only clear
        now = ctx.clock.now()
        window_hours = ctx.config.runner_ceiling_window_hours
        totals = ctx.stores.usage.usage_since(now - timedelta(hours=window_hours))
        if not totals.reaches(cap):
            return
        reason = spend_ceiling_reason(
            cap=cap, window_hours=window_hours, spend=totals.cost_usd, partial=totals.cost_partial
        )
        _log.warning(
            f"runner locally paused — {reason}",
            runner_id=ctx.config.runner_id,
            ceiling_usd=cap,
            spend_usd=totals.cost_usd,
            window_hours=ctx.config.runner_ceiling_window_hours,
            cost_partial=totals.cost_partial,
        )
        PauseService(ctx.stores.pause, ctx.clock, events=ctx.events).engage(
            ctx.config.runner_id, by="runner-ceiling", reason=reason
        )


class Reap(Step):
    def run(self) -> None:
        """Expire leases whose worker is gone or **stalled** — each a failed attempt.

        An **orphan** (minted but never spawned) and a **stalled-but-alive** worker end here.
        An exited session-bearing worker is ADVANCE's: exit is the done declaration, and the
        conservative staleness threshold is what keeps the two apart."""
        ctx = self.ctx
        _CP_REAP_BEFORE.reached()
        braked = not Spawner(ctx).brakes().starts_processes
        now = ctx.clock.now()
        parked = ctx.stores.asks.parked_lease_ids()
        takeovers = TakeoverHolds.of(ctx.stores.takeover.open_takeovers())
        active_leases = ctx.stores.lease_record.list_active_leases()
        # One bulk read for every candidate's heartbeat/spawn (`bzh:bulk-reconstitution`),
        # rather than two reads per lease.
        liveness_facts = ctx.stores.liveness.liveness_facts([lease.lease_id for lease in active_leases])
        deferred = 0
        for lease in active_leases:
            facts = liveness_facts.get(lease.lease_id)
            liveness = Liveness.of(
                lease,
                heartbeat=facts.latest_heartbeat if facts is not None else None,
                spawn=facts.latest_spawn if facts is not None else None,
            )
            move = reap_move(
                lease,
                taken_over=takeovers.holds_lease(lease),
                parked=lease.lease_id in parked,
                alive=lease.pid is not None and ctx.process.is_alive(lease.pid, lease.process_start_time or ""),
                stale=liveness.stale(now),
                braked=braked,
            )
            if move is ReapMove.REAP_UNSPAWNED:
                if lease.pid is not None:
                    # A durably-provisional generation: close it unidentified before
                    # failing the attempt, so REAP doesn't leave the generation ambiguously open.
                    ctx.stores.liveness.record_identity_failed(lease.lease_id, at=now)
                _log.info("reaping unspawned lease", lease_id=lease.lease_id, chunk_id=lease.chunk_id)
                Attempt(ctx, lease).fail(reason=LeaseClosureReason.REAPED, via="reap")
            elif move is ReapMove.DEFER:
                deferred += 1
            elif move is ReapMove.REAP_STALLED:
                _log.info("reaping stalled worker", lease_id=lease.lease_id, chunk_id=lease.chunk_id, pid=lease.pid)
                Attempt(ctx, lease).fail(reason=LeaseClosureReason.REAPED, via="reap")
        if deferred:
            _log.info("reap deferred — locally paused", runner_id=ctx.config.runner_id, count=deferred)
        _CP_REAP_AFTER.reached()


@dataclass(frozen=True)
class ResumeIntents:
    """Marking in-flight leases for same-lease restart-resume — the graceful-shutdown hook
    and the startup crash-orphan scan. Store-only: no context, no hub.

    Spans leases, asks (parked) and outbound (pending submission), so it holds the
    :class:`~blizzard.runner.stores.RunnerStores` bundle."""

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

    def _resumable(self) -> Iterator[Lease]:
        """Active, session-bearing leases that are neither parked, mid-submission, nor
        mid-elicitation — an unspawned one is REAP's residue, with nothing to resume.

        The elicitation exclusion matters on both callers: a graceful
        restart-resume would otherwise wake a second process on the same session, and an
        ungraceful crash-orphan scan would otherwise leave the pre-resume elicitation's stale
        record to be misread as the resumed generation's own verdict — neither path may
        re-mint or resume a lease whose elicitation is in flight. A backing-off lease is
        excluded the same way: its own ``resume_after`` is already durable,
        and either restart path re-marking it would wake it early, skipping the wait."""
        parked = self.stores.asks.parked_lease_ids()
        pending = self.stores.outbound.pending_submission_lease_ids()
        eliciting = self.stores.elicitations.in_flight_elicitation_lease_ids()
        backing_off = backing_off_facts(self.stores.overload, self.stores.liveness, self.stores.elicitations)
        takeovers = TakeoverHolds.of(self.stores.takeover.open_takeovers())
        for lease in self.stores.lease_record.list_active_leases():
            if resumable(
                lease,
                parked=parked,
                pending_submission=pending,
                eliciting=eliciting,
                backing_off=backing_off,
                taken_over=takeovers.holds_lease(lease),
            ):
                yield lease

    def _crash_orphaned(self, ended: Container[str], process: IProcessProbe, crashed_at: datetime) -> Iterator[Lease]:
        # Declared done (SessionEnd fired) is ADVANCE's to judge; orphaned-but-alive is REAP's to re-adopt;
        # gone stale before the crash, ADVANCE judges the dead session, a retry consumed only if no verdict comes.
        for lease in self._resumable():
            liveness = Liveness.of(
                lease,
                heartbeat=self.stores.liveness.latest_heartbeat(lease.lease_id),
                spawn=self.stores.liveness.latest_spawn(lease.lease_id),
            )
            if crash_orphaned(
                lease,
                session_ended=lease.lease_id in ended,
                alive=lease.pid is not None and process.is_alive(lease.pid, lease.process_start_time or ""),
                stale=liveness.stale(crashed_at),
            ):
                yield lease

    def _mark(self, leases: Iterator[Lease], *, now: datetime) -> int:
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
        takeovers = TakeoverHolds.of(ctx.stores.takeover.open_takeovers())
        for lease_id in intents:
            lease = active.get(lease_id)
            if lease is None:
                ctx.stores.resume_intent.record_resume_clear(lease_id=lease_id, cleared_at=ctx.clock.now())
                continue
            if takeovers.holds_lease(lease):
                continue  # a person holds this session — the intent waits for the takeover to end
            DormantSession(ctx, lease).restart_or_release(Fenced(takeovers))


class Pull(Step):
    def run(self) -> None:
        """Exchange facts with the hub: sync the registry, reconcile ownership, drain the buffer.

        Reconciliation runs BEFORE the drain, so a preempted lease's queued submission still
        reaches the hub and the drain absorbs the stale-epoch rejection against a lease already
        closed — the retry budget the move must not spend is never reached."""
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
                capabilities=ctx.capability_snapshot(),
                subscriptions=tuple(
                    RunnerSubscriptionDeclaration(slug=s.slug, name=s.name, provider=s.provider)
                    for s in ctx.subscriptions
                ),
                gates=ctx.config.gates,
            )
            paused = ctx.hub.fetch_runner_paused(ctx.config.runner_id)
        except HubClientError:
            return  # hub unreachable — keep the last-mirrored brake
        ctx.stores.pause.set_hub_paused(ctx.config.runner_id, paused=paused, at=ctx.clock.now())

    def _reconcile_leases(self) -> None:
        """Reconcile every active lease against the hub's view of its chunk — abandon it if the hub
        no longer routes it here, park it if the operator paused it, preempt it if a
        restart moved the chunk out from under it. All three share **one** ``ctx.chunk_views.get``
        per lease — a per-tick cache primed at tick start, not a fresh hub round trip
        each time — and a transport failure reads as none of them. The pause branch keys on the
        pause *fact*, which an ask-park masks."""
        ctx = self.ctx
        pause_parked = ctx.stores.pause.pause_parked_lease_ids()  # hoisted: the park guard, one read per tick
        fenced = Fenced(TakeoverHolds.of(ctx.stores.takeover.open_takeovers()))
        for lease in ctx.stores.lease_record.list_active_leases():
            try:
                view = ctx.chunk_views.get(lease.chunk_id)
            except ChunkNotFoundError:
                # Terminal, not retryable. Ordered before the HubClientError arm
                # because it subclasses it, or the 404 would be swallowed as "hub unreachable".
                Attempt(ctx, lease).abandon(via="pull")
                continue
            except HubClientError:
                continue  # hub unreachable — last-known directive holds; keep working
            move = lease_reconcile_move(
                view,
                lease,
                runner_id=ctx.config.runner_id,
                pause_parked=pause_parked,
                fenced=fenced.out(view, lease),
            )
            if move is LeaseReconcileMove.ABANDON:
                Attempt(ctx, lease).abandon(via="pull")
            elif move is LeaseReconcileMove.PARK:
                Attempt(ctx, lease).park_paused(via="pull")
            elif move is LeaseReconcileMove.PREEMPT:
                Attempt(ctx, lease).preempt(via="pull")

    def _reconcile_escalations(self) -> None:
        """Close a local escalation on every arm that supersedes one (domain:
        escalation.md#Supersession) — one ``ctx.chunk_views.get`` each, the same per-tick cache
        read ``_reconcile_leases`` above makes. An escalated lease is already
        closed, so ``_reconcile_leases`` above never sees it; the fourth arm, this runner's own
        next lease mint, never reaches this read either, filtered out of ``open_escalations()`` by
        ``LIVE_ESCALATION`` before it gets here. The remaining three collapse into one condition —
        the hub no longer routes this chunk to this runner at this epoch — shared with the routing
        and fencing reads ``_reconcile_leases`` makes for its own leases: requeued away (route
        gone), reassigned to another runner (route moved), or an operator restart at or past this
        epoch (``Fenced``). The mark is what keeps the read hub-free (``bzh:facts-not-status``)."""
        ctx = self.ctx
        fenced = Fenced(TakeoverHolds.of(ctx.stores.takeover.open_takeovers()))
        for escalation in ctx.stores.escalations.open_escalations():
            try:
                view = ctx.chunk_views.get(escalation.chunk_id)
            except HubClientError as exc:
                # Covers ChunkNotFoundError: an unknown chunk is not a resolution.
                _log.debug("escalation left open — hub unreadable", chunk_id=escalation.chunk_id, error=str(exc))
                continue
            superseded = escalation.superseded_by(
                view, runner_id=ctx.config.runner_id, fenced_out=fenced.out(view, escalation)
            )
            if not superseded:
                _log.debug("escalation left open", chunk_id=escalation.chunk_id, hub_status=view.status.value)
                continue
            ctx.stores.escalations.record_escalation_closure(
                chunk_id=escalation.chunk_id, reason=view.status.value, at=ctx.clock.now()
            )
            if ctx.events is not None:
                ctx.events.publish_escalation_changed(
                    escalation.chunk_id,
                    cause="closed",
                    lease_id=escalation.lease_id,
                )

    def _reconcile_takeovers(self) -> None:
        """Close an open takeover whose chunk the hub has ended — one
        ``ctx.chunk_views.get`` each, the same per-tick cache read the other two reconcile
        sweeps make. The takeover fact now authorizes the resumed session's
        worker verbs, so a chunk the hub ends mid-takeover must not leave that
        authorization standing forever; this is the second, no-person-drives closer alongside
        the CLI's own end-PATCH. The mark is what keeps the read hub-free (``bzh:facts-not-status``)."""
        ctx = self.ctx
        for takeover in ctx.stores.takeover.open_takeovers():
            try:
                view = ctx.chunk_views.get(takeover.chunk_id)
            except HubClientError as exc:
                # Covers ChunkNotFoundError: an unknown chunk is not a resolution.
                _log.debug("takeover left open — hub unreadable", chunk_id=takeover.chunk_id, error=str(exc))
                continue
            if not takeover.ended_by(view):
                _log.debug("takeover left open", chunk_id=takeover.chunk_id, hub_status=view.status.value)
                continue
            TakeoverCloser(ctx.stores.takeover, ctx.clock, ctx.events).close(
                TakeoverCloseScope(chunk_id=takeover.chunk_id, open_takeover=takeover), takeover.takeover_id
            )


class Fill(Step):
    def run(self) -> None:
        """Keep the fleet busy: peek → acquire → claim-by-route → bind → spawn.

        Open slots are ``MAX_AGENTS - active_leases``; for each, peek the ready queue, acquire the
        chunk's environments all-or-nothing, and claim the route. Either brake stops *new* claims.
        """
        ctx = self.ctx
        brakes = Spawner(ctx).brakes()
        InterruptedClaims(ctx).reconcile(braked=brakes.blocks_claims)
        if brakes.blocks_claims:
            _log.info(
                "paused — no new claims this tick",
                runner_id=ctx.config.runner_id,
                hub_paused=brakes.hub,
                local_paused=brakes.local,
            )
            return
        slots = open_slots(ctx.config.max_agents, ctx.stores.lease_record.count_active_leases())
        # Whether this runner asserts capabilities at all is a registry-shape question —
        # read from the registry, never by building a snapshot that probes every binary.
        if ctx.harnesses.known_harnesses:
            # A capability-asserting runner peeks per attempt, not once per fill
            # — the single-entry peek response leaves no cache to reuse.
            for _ in range(slots):
                if not ReadyQueue.peeked(ctx).claim_one():
                    break
        else:
            queue = ReadyQueue.peeked(ctx)  # one hub peek for the whole fill — legacy path only
            for _ in range(slots):
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
        open_parks = ctx.stores.pause.open_pause_parks()  # hoisted: the parks' teardown facts, one read per tick
        in_flight_elicitations = ctx.stores.elicitations.in_flight_elicitations_by_lease()  # hoisted: same, by lease id
        backing_off = backing_off_facts(ctx.stores.overload, ctx.stores.liveness, ctx.stores.elicitations)
        resume_intents = ctx.stores.resume_intent.resume_intent_lease_ids()
        takeovers = TakeoverHolds.of(ctx.stores.takeover.open_takeovers())
        for lease in ctx.stores.lease_record.list_active_leases():
            park = open_parks.get(lease.lease_id)
            move = advance_move(
                lease,
                taken_over=takeovers.holds_lease(lease),
                resume_marked=lease.lease_id in resume_intents,
                pending_submission=lease.lease_id in pending,
                pause_parked=park is not None,
                ask_parked=lease.lease_id in ask_parked,
                backing_off=lease.lease_id in backing_off,
                alive=lease.pid is not None and ctx.process.is_alive(lease.pid, lease.process_start_time or ""),
            )
            if move is AdvanceMove.ON_UNPAUSE and park is not None:
                # Dormant on an operator pause — finish the park's teardown, resume when it lifts.
                DormantSession(ctx, lease).on_unpause(park, in_flight_elicitations.get(lease.lease_id))
            elif move is AdvanceMove.ON_ANSWER:
                DormantSession(ctx, lease).on_answer()  # dormant on a question — resume on the answer
            elif move is AdvanceMove.ON_OVERLOAD_BACKOFF:
                # No-ops until `resume_after` passes, then wakes the same lease/epoch/session in place.
                DormantSession(ctx, lease).on_overload_backoff(backing_off[lease.lease_id])
            elif move is AdvanceMove.EXITED:
                self._advance_exited_worker(lease)

        # Read AFTER the loop above, not the same set it started with (`bzh:bulk-reconstitution`):
        # that loop can close a lease whose chunk this one must now drive in the same pass.
        active_chunk_ids = {lease.chunk_id for lease in ctx.stores.lease_record.list_active_leases()}
        for chunk_id in ctx.stores.environments.live_tenure_chunk_ids():
            if chunk_id not in active_chunk_ids:
                HeldChunk(ctx, chunk_id).drive(takeovers)

    def _advance_exited_worker(self, lease: Lease) -> None:
        """Collect an in-flight elicitation, else park on an open ask, else launch the verdict
        elicitation.

        The in-flight check runs BEFORE the ask pre-check: once a launch is durable, this
        lease's every later pass is a collect, not a fresh judge — and collecting must not be
        pre-empted by an ask the worker raised *during its live turns, before it exited* (the
        ordinary ask-and-exit shape below). An ask raised *during the elicitation itself* is a
        different case, handled inside `Judgement._judged` after the verdict parse returns
        ``None`` — this pre-check cannot see that one; it is recorded mid-elicitation."""
        if lease.session_id is None:
            return  # not spawned — REAP's residue (guarded by the caller too)
        elicitation = self.ctx.stores.elicitations.in_flight_elicitation(lease.lease_id, lease.epoch)
        if elicitation is not None:
            if elicitation_still_pending(self.ctx, elicitation):
                # Live and under the staleness bound — the steady-state case. `collect`
                # would early-return here anyway; skip the envelope/binding fetch it never uses.
                return
            judgement = Judgement.of(self.ctx, lease)
            if judgement is not None:
                judgement.collect(elicitation)
            return
        # A usage-limited generation is classified ahead of the ask pre-check and judging
        # alike: the exit is neither an ask nor a verdict to judge, it is the
        # harness itself reporting it could not run at all.
        generation = self.ctx.stores.liveness.lease_generation(lease.lease_id)
        output = self.ctx.worker_files.read_stdout(lease.lease_id, generation)
        bindings = self.ctx.stores.environments.bindings_for_chunk(lease.chunk_id)
        lines = self.ctx.usage.worker_transcript_lines(lease, bindings, generation=generation)
        limit = classify_worker_usage_limit(self.ctx, lease, output, lines)
        if limit is not None:
            engage_and_park_worker(self.ctx, lease, limit)
            return
        # A provider-overloaded generation is classified right alongside the usage limit
        # — the two are mutually exclusive exit reasons for the one exit,
        # both read from the same `output`/`lines` pair read once above.
        overload = classify_worker_overload(self.ctx, lease, output, lines)
        if overload is not None:
            if record_worker_overload(self.ctx, lease, overload, generation=generation):
                return  # backing off in place — the next tick's `backing_off_facts` picks it up
            # Streak limit reached: fall through to today's ordinary path below.
        else:
            reset_if_streak_open(self.ctx, lease)
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


class Retention(Step):
    """Prune every append-only observation/report lane once per floor, so none grows without
    bound — each lane's own retention contract lives at its own method (the
    store's `IWriteOutboundRepository.prune_outbound` and its siblings, or the filesystem
    sweep of `WorkerStdoutFiles.sweep`), each its own isolated prune. The shortest window is a
    day, so the pass runs when none has yet or `RETENTION_FLOOR` has elapsed since the last —
    `bzh:probe-gated-pass`'s floor-only form."""

    def run(self) -> None:
        """One prune per lane, each isolated (mirrors ExternalUsageSample's own per-item
        loop): one lane's failure must not skip the others, and this step gates nothing
        else in the tick either way."""
        ctx = self.ctx
        now = ctx.clock.now()
        passes = ctx.retention_passes
        if passes is not None and not passes.due(now):
            return
        lanes: tuple[tuple[str, Callable[[], int]], ...] = (
            ("outbound buffer", lambda: ctx.stores.outbound.prune_outbound(now=now)),
            ("heartbeat", lambda: ctx.stores.liveness.prune_heartbeats(now=now)),
            ("external usage sample", lambda: ctx.stores.usage.prune_external_usage_samples(now=now)),
            (
                "worker stdout",
                lambda: ctx.worker_files.sweep(
                    now=now, retention=timedelta(days=ctx.config.worker_stdout_retention_days)
                ),
            ),
        )
        for label, prune in lanes:
            try:
                prune()
            except Exception as exc:  # one lane's prune failure must not skip the others
                _log.warning("retention prune failed", lane=label, detail=str(exc))
        # Every lane was attempted, whatever its outcome: a lane that raised retries at the next floor.
        if passes is not None:
            passes.record(now)


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
        if warn_tokens is None or not ctx.transcripts_wired:
            return
        try:
            leases = ctx.stores.lease_record.list_active_leases()
            # One bulk read for the whole active set (`bzh:bulk-reconstitution`); each
            # lease below filters "due" itself from its own already-fetched state.
            states = ctx.stores.usage.context_sample_states([lease.lease_id for lease in leases])
            bindings_by_chunk = group_bindings_by_chunk(ctx.stores.environments.held_bindings())
        except Exception as exc:  # this step is not last in the tick — see ExternalUsageSample
            _log.warning("context sample step failed", detail=str(exc))
            return
        for lease in leases:
            try:
                self._sample(lease, warn_tokens, states.get(lease.lease_id), bindings_by_chunk.get(lease.chunk_id, []))
            except Exception as exc:  # one lease's read must not end the sweep
                _log.warning("context sample failed", lease_id=lease.lease_id, detail=str(exc))

    def _sample(
        self,
        lease: Lease,
        warn_tokens: int,
        state: ContextSampleState | None,
        bindings: list[EnvBinding],
    ) -> None:
        ctx = self.ctx
        session = lease.session
        if session is None or not ctx.transcripts_wired:
            return  # a lease whose spawn has not yet minted a session has nothing to read
        now = ctx.clock.now()
        interval = timedelta(seconds=ctx.config.context_sample_interval_seconds)
        if not ContextSampleState.sample_due(state, now=now, interval=interval):
            return
        try:
            source = ctx.transcript_source_for(session)
        except (UnknownHarnessError, UnavailableHarnessError) as exc:
            # A read-only side lane: never escalates on its own, only skips this one lease's
            # sample for this tick — its own dispatch path (a wake, a judgement) escalates it.
            _log.warning(
                "context sample skipped — owner unresolvable",
                lease_id=lease.lease_id,
                session_id=session.session_id,
                harness_id=session.harness_id,
                reason="unavailable" if isinstance(exc, UnavailableHarnessError) else "unknown",
                detail=str(exc),
            )
            return
        tokens = source.context_tokens(session.session_id, spawn_cwd=self._spawn_cwd(bindings))
        crossing = ContextSampleState.first_crossing(state, tokens=tokens, warn_tokens=warn_tokens)
        seq = ctx.stores.usage.record_context_sample(
            lease_id=lease.lease_id,
            chunk_id=lease.chunk_id,
            session=session,
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
    def _event(lease: Lease, tokens: int | None, warn_tokens: int, now: datetime) -> dict[str, object]:
        """The ``event.recorded`` payload one crossing surfaces, in the shape the hub ingests."""
        return {
            "severity": EVENT_LOG_SEVERITY[_CONTEXT_WARNED],
            "kind": _CONTEXT_WARNED,
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

    def _spawn_cwd(self, bindings: list[EnvBinding]) -> str | None:
        """The lease's worktree, the transcript locator's multi-match tie-break — never its key.

        Resolved exactly as the transcript pump resolves it, so both lanes read the same file."""
        return SpawnCwd(self.ctx.config.workspace_root, bindings[0].workdir if bindings else None).path


class ExternalUsageSample(Step):
    """Every declared subscription's own rate-limit utilization, each on
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
        if not sample_due(anchor, ctx.clock.now(), resolved.sample_interval_seconds):
            return
        sampler = resolved.sampler
        if sampler is None:
            # A declaration its provider binds no sampler to stays declared and unsampled, with no
            # attempt row, since no sampler failed.
            return
        # Renewal, if this provider binds one, runs before the sample on this same cadence
        # gate, never its own; a failed or not-due renewal never stops the sample.
        renewal = self._renewal_value(resolved)
        attempt = external_usage_attempt(sampler.sample(), slug=resolved.slug, renewal=renewal, at=ctx.clock.now())
        result = attempt.result
        if isinstance(result, SampleMissReason):
            payload = None
            report_payload = json.dumps(self._miss_payload(resolved, result, missed_at=attempt.sampled_at))
        else:
            payload = report_payload = json.dumps(self._payload(resolved, result))
        seq = ctx.stores.usage.record_external_usage_attempt(
            slug=attempt.slug,
            sampled_at=attempt.sampled_at,
            payload=payload,
            report_kind=attempt.report_kind,
            report_payload=report_payload,
            miss_reason=attempt.miss_reason.value if attempt.miss_reason is not None else None,
            renewal=attempt.renewal,
        )
        if seq is not None and ctx.events is not None:
            ctx.events.publish_fact_changed(seq=seq, kind=attempt.report_kind, chunk_id=None, lease_id=None)

    @staticmethod
    def _renewal_value(resolved: ResolvedSubscription) -> str | None:
        """Asks this slug's renewer, if it has one, and reduces its outcome to the single
        string the attempt row's ``renewal`` column carries — ``None`` for both "no
        renewer" and "not due", since neither is a renewal outcome worth showing;
        never raises, so a broken renewer never stops the sample that follows it."""
        if resolved.renewer is None:
            return None
        try:
            outcome = resolved.renewer.renew_if_due()
        except Exception as exc:  # second line of defense — the renewer contract already promises this
            _log.warning("credential renewal failed unexpectedly", slug=resolved.slug, detail=str(exc))
            return None
        return outcome.recorded_value

    @staticmethod
    def _miss_payload(
        resolved: ResolvedSubscription, reason: SampleMissReason, *, missed_at: datetime
    ) -> dict[str, object]:
        """The stable JSON shape for a sampler miss — exactly ``{slug,
        name, missed_at, reason}``, the reason only: never a token, a refresh token, or a
        path crosses on a miss."""
        return {
            "slug": resolved.slug,
            "name": resolved.name,
            "missed_at": iso_utc(missed_at),
            "reason": reason.value,
        }

    @staticmethod
    def _payload(resolved: ResolvedSubscription, snapshot: ExternalSubscriptionUsageSnapshot) -> dict[str, object]:
        """The stable JSON shape for a sampled snapshot — both this attempt's stored
        ``payload`` and its buffered outbound report use this exact shape. ``slug`` and
        ``name`` name the declared subscription and its operator-facing label. A window
        the hub's own fact model would refuse is dropped here rather than sent, so the
        persisted attempt and the outbound report carry the same complete windows."""
        windows: list[dict[str, object]] = []
        for window in snapshot.windows:
            payload = {
                "window": window.window,
                "utilization_pct": window.utilization_pct,
                "resets_at": iso_utc(window.resets_at),
                "window_seconds": window.window_seconds,
            }
            try:
                ExternalSubscriptionUsageWindowFact.model_validate(payload)
            except ValidationError as exc:
                _log.warning(
                    "dropped malformed external usage window",
                    slug=resolved.slug,
                    window=window.window,
                    reason=exc.errors()[0]["type"] if exc.errors() else "invalid",
                )
                continue
            windows.append(payload)
        return {
            "slug": resolved.slug,
            "name": resolved.name,
            "sampled_at": iso_utc(snapshot.sampled_at),
            "windows": windows,
        }
