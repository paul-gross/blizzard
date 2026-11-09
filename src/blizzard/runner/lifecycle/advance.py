"""The ADVANCE sweep: judge each exited worker and drive every held chunk through its graph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from blizzard.runner.environments.repository import EnvBinding, IReadEnvironmentRepository, group_bindings_by_chunk
from blizzard.runner.hub.outbound_buffer import IWriteOutboundRepository
from blizzard.runner.leases.asks import IWriteAskRepository
from blizzard.runner.leases.elicitation import IWriteElicitationRepository, PendingElicitation
from blizzard.runner.leases.liveness import IWriteLeaseLivenessRepository
from blizzard.runner.leases.model import Lease
from blizzard.runner.leases.overload import IWriteOverloadRepository, backing_off_facts
from blizzard.runner.leases.record import IWriteLeaseRecordRepository
from blizzard.runner.leases.resume_intent import IWriteLeaseResumeIntentRepository
from blizzard.runner.lifecycle.attempt import Attempt
from blizzard.runner.lifecycle.dormant import DormantSession, DormantStores
from blizzard.runner.lifecycle.held_chunk import HeldChunk, HeldChunkContext
from blizzard.runner.lifecycle.judgement.judgement import (
    Judgement,
    JudgementContext,
    KnownElicitation,
    elicitation_still_pending,
)
from blizzard.runner.lifecycle.model import (
    AdvanceMove,
    AwaitElicitation,
    BackOff,
    Classify,
    CloseOverloadStreak,
    CollectElicitation,
    Judge,
    ParkOnAsk,
    ParkOnUsageLimit,
    RecordOverload,
    TakeoverHolds,
    advance_move,
    exited_worker_classified_move,
    exited_worker_elicitation_move,
    exited_worker_settled_move,
)
from blizzard.runner.lifecycle.orphan import orphan_resultless_exit
from blizzard.runner.lifecycle.spawn import Spawner
from blizzard.runner.lifecycle.takeover import IReadTakeoverRepository
from blizzard.runner.lifecycle.usage_limit import classify_worker_usage_limit, mark_and_park_worker
from blizzard.runner.throttle.overload import classify_worker_overload, record_worker_overload, reset_if_streak_open
from blizzard.runner.throttle.pause import IWritePauseRepository, PausePark, RunnerBrakes


class AdvanceStores(DormantStores, Protocol):
    """The repositories the sweep's own body reads, each at the type its base already declares."""

    @property
    def outbound(self) -> IWriteOutboundRepository: ...
    @property
    def asks(self) -> IWriteAskRepository: ...
    @property
    def pause(self) -> IWritePauseRepository: ...
    @property
    def elicitations(self) -> IWriteElicitationRepository: ...
    @property
    def environments(self) -> IReadEnvironmentRepository: ...
    @property
    def overload(self) -> IWriteOverloadRepository: ...
    @property
    def liveness(self) -> IWriteLeaseLivenessRepository: ...
    @property
    def resume_intent(self) -> IWriteLeaseResumeIntentRepository: ...
    @property
    def takeover(self) -> IReadTakeoverRepository: ...
    @property
    def lease_record(self) -> IWriteLeaseRecordRepository: ...


class AdvanceContext(JudgementContext, HeldChunkContext, Protocol):
    """What the ADVANCE sweep reads: every collaborator's context, and the stores it reads itself."""

    @property
    def stores(self) -> AdvanceStores: ...


@dataclass(frozen=True)
class AdvanceSweep:
    ctx: AdvanceContext

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
        bindings_by_chunk = group_bindings_by_chunk(
            ctx.stores.environments.held_bindings()
        )  # hoisted: one read, a pass
        # Read when a pause-parked lease first needs it, and dropped after each exited-worker handler: that
        # path alone engages brakes, and a later lease must not wake under a value read before one engaged.
        brakes: RunnerBrakes | None = None
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

            def drive(lease: Lease = lease, move: AdvanceMove = move, park: PausePark | None = park) -> None:
                nonlocal brakes
                if move is AdvanceMove.ON_UNPAUSE and park is not None:
                    if brakes is None:
                        brakes = Spawner(ctx).brakes()
                    DormantSession(ctx, lease).on_unpause(
                        park,
                        in_flight_elicitations.get(lease.lease_id),
                        bindings=bindings_by_chunk.get(lease.chunk_id, []),
                        ask_parked=lease.lease_id in ask_parked,
                        brakes=brakes,
                    )
                elif move is AdvanceMove.ON_ANSWER:
                    DormantSession(ctx, lease).on_answer()
                elif move is AdvanceMove.ON_OVERLOAD_BACKOFF:
                    DormantSession(ctx, lease).on_overload_backoff(backing_off[lease.lease_id])
                elif move is AdvanceMove.EXITED:
                    self._advance_exited_worker(
                        lease, bindings_by_chunk.get(lease.chunk_id, []), in_flight_elicitations.get(lease.lease_id)
                    )
                    brakes = None

            # One lease's refused launch escalates that lease alone; the pass goes on to the rest.
            if Attempt(ctx, lease).escalating_refused_launch(drive, via=f"advance-{move.value}"):
                brakes = None  # the handler it cut short may have engaged a brake, as the exited path does

        # Read AFTER the loop above, not the same set it started with (`bzh:bulk-reconstitution`):
        # that loop can close a lease whose chunk this one must now drive in the same pass.
        active_chunk_ids = ctx.stores.lease_record.active_lease_chunk_ids()
        for chunk_id in ctx.stores.environments.live_tenure_chunk_ids():
            if chunk_id not in active_chunk_ids:
                HeldChunk(ctx, chunk_id).drive(takeovers)

    def _advance_exited_worker(
        self, lease: Lease, bindings: list[EnvBinding], hoisted_elicitation: PendingElicitation | None
    ) -> None:
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
        ctx = self.ctx
        # The tick's map is keyed by lease alone; the singular read it replaces also matched the epoch.
        elicitation = (
            hoisted_elicitation
            if hoisted_elicitation is not None and hoisted_elicitation.epoch == lease.epoch
            else None
        )
        match exited_worker_elicitation_move(
            elicitation=elicitation,
            still_pending=elicitation is not None and elicitation_still_pending(ctx, elicitation),
        ):
            case AwaitElicitation():
                return
            case CollectElicitation(elicitation=launched):
                judgement = Judgement.of(ctx, lease, bindings=bindings)
                if judgement is not None:
                    judgement.collect(launched)
                return
            case Classify():
                pass
        # A usage limit and a provider overload are classified ahead of the ask pre-check and
        # judging alike: the exit is neither an ask nor a verdict to judge, it is the harness
        # itself reporting it could not run at all. Both read the one `output`/`lines` pair.
        generation = ctx.stores.liveness.lease_generation(lease.lease_id)
        output = ctx.worker_files.read_stdout(lease.lease_id, generation)
        # Ahead of every move below: a worker gone without its result may have left its turn running past it.
        orphan_resultless_exit(ctx, lease, output, via="exit")
        lines = ctx.usage.worker_transcript_lines(lease, bindings, generation=generation)
        limit = classify_worker_usage_limit(ctx, lease, output, lines)
        overload = None if limit is not None else classify_worker_overload(ctx, lease, output, lines)
        backing_off = False
        match exited_worker_classified_move(limit=limit, overload=overload):
            case ParkOnUsageLimit(limit=parked):
                mark_and_park_worker(ctx, lease, parked, generation=generation)
                return
            case RecordOverload(overload=recorded):
                # False at the streak limit: fall through to today's ordinary path below.
                backing_off = record_worker_overload(ctx, lease, recorded, generation=generation)
            case CloseOverloadStreak():
                reset_if_streak_open(ctx, lease)
        # Ask-and-exit: an exit holding an unforwarded ask is a park, an exit with neither is a
        # failure. Not a spawn, so it proceeds regardless of the local brake.
        match exited_worker_settled_move(backing_off=backing_off, ask=ctx.stores.asks.unforwarded_ask(lease.lease_id)):
            case BackOff():
                return  # backing off in place — the next tick's `backing_off_facts` picks it up
            case ParkOnAsk(ask=ask):
                DormantSession(ctx, lease).park_on_ask(ask)
                return
            case Judge():
                pass
        judgement = Judgement.of(
            ctx, lease, bindings=bindings, generation=generation, known_elicitation=KnownElicitation(elicitation)
        )
        if judgement is not None:
            judgement.run()

        # Every other shape keeps its binding and is polled again next tick.
