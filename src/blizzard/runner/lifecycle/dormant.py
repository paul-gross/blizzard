"""A lease whose worker is gone but whose session survives — parking into it, and waking it."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.crash import crashpoint
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.environments.repository import EnvBinding
from blizzard.runner.harness.adapter import IHarnessWorkerLifecycle
from blizzard.runner.harness.registry import UnavailableHarnessError, UnknownHarnessError
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.hub.client import ChunkNotFoundError, HubClientError
from blizzard.runner.hub.outbound import OutboundFacts
from blizzard.runner.leases import Lease
from blizzard.runner.leases.asks import OpenAsk
from blizzard.runner.leases.elicitation import PendingElicitation
from blizzard.runner.leases.operator_requests import IReadAttachmentRepository
from blizzard.runner.leases.overload import OverloadExit
from blizzard.runner.lifecycle.attempt import Attempt
from blizzard.runner.lifecycle.judgement.check_runner import ICheckRunner
from blizzard.runner.lifecycle.judgement.checks import IWriteCheckRepository
from blizzard.runner.lifecycle.judgement.git_commits import GitCommitsContext, GitCommitsStores
from blizzard.runner.lifecycle.model import (
    Fenced,
    RestartDisposition,
    UnpauseMove,
    answer_ready,
    park_names_elicitation,
    pause_park_drain_expired,
    restart_disposition,
    unpause_move,
)
from blizzard.runner.lifecycle.registration import registered_runner_id
from blizzard.runner.lifecycle.spawn import SpawnConfig, Spawner
from blizzard.runner.lifecycle.usage_limit import UsageLimitContext, UsageLimitStores
from blizzard.runner.process.owned_process import kill_owned_process, owned_process_alive
from blizzard.runner.throttle.overload import OverloadContext, OverloadStores
from blizzard.runner.throttle.pause import PausePark
from blizzard.runner.transcripts.invocation_boundaries import worker_boundary_open
from blizzard.runner.usage.recorder import UsageRecorder

_log = get_logger("blizzard.runner.loop")

#: The message RESUME delivers into a marked session on a restart — ``#``-prefixed so it is
#: inert in prose and in a behavior script alike. The exact prose is unpinned.
_RESTART_MESSAGE = "# The supervisor restarted; continue your task where you left off."

#: The message ADVANCE delivers into a session the operator paused and resumed.
#: Same inert ``#``-prefixed framing; the exact prose is unpinned.
_UNPAUSE_MESSAGE = "# The operator resumed this chunk; continue your task where you left off."

#: The message a worker generation's own overload backoff delivers on wake.
#: Same inert ``#``-prefixed framing; the exact prose is unpinned.
_OVERLOAD_BACKOFF_MESSAGE = "# The provider was overloaded; retrying automatically."

# The restart re-attach. `_wake`'s own middle (a resumed process launched but not yet
# durably recorded) is armed exactly like SPAWN's two-phase mint, below; recovery
# re-runs RESUME idempotently regardless of which of these four windows a crash lands in.
_CP_RESUME_AFTER_KILL = crashpoint("resume.after-kill.before-reattach", "survivor killed; session not yet re-attached")
_CP_RESUME_AFTER = crashpoint("resume.after-reattach", "session re-attached under the same lease; intent cleared")
_CP_WAKE_AFTER_LAUNCH = crashpoint(
    "resume.wake.after-launch.before-record", "resumed process exists; pid not yet durable"
)
_CP_WAKE_AFTER_RECORD = crashpoint("resume.wake.after-record", "resumed pid durably recorded; launch not yet disarmed")
# The transcript invocation boundary: a plain resume's own new pre-launch write.
_CP_WAKE_AFTER_BOUNDARY = crashpoint(
    "resume.wake.after-boundary-record.before-launch", "resume invocation boundary durable; session not yet resumed"
)


class DormantStores(UsageLimitStores, GitCommitsStores, OverloadStores, Protocol):
    @property
    def attachments(self) -> IReadAttachmentRepository: ...
    @property
    def checks(self) -> IWriteCheckRepository: ...


class DormantConfig(SpawnConfig, Protocol):
    @property
    def gates(self) -> tuple[str, ...]: ...


class DormantContext(UsageLimitContext, GitCommitsContext, OverloadContext, Protocol):
    @property
    def stores(self) -> DormantStores: ...
    @property
    def config(self) -> DormantConfig: ...
    @property
    def usage(self) -> UsageRecorder: ...
    @property
    def check_runner(self) -> ICheckRunner | None: ...


@dataclass(frozen=True)
class DormantSession:
    """A lease with no live worker but a session still resumable under it — parked on a
    question, parked on an operator pause, or marked for restart-resume.

    Every wake here rewrites only ``pid``/``process_start_time``: same lease, same epoch, same
    session, so **no retry is consumed** by going dormant and coming back."""

    ctx: DormantContext
    lease: Lease

    def resume_on_unmet_produces(self, message: str, bindings: list[EnvBinding]) -> None:
        """Resume a session that exited with required ``produces:`` unattached, instead of
        judging it — no retry consumed, no epoch bumped. Owner resolution is
        checked **before** the generation's spend is recorded (the same "resolve before any
        mutation" shape :meth:`_restart` uses), so an unresolvable owner escalates in place
        rather than recording a spend for a wake that never happens."""
        lease = self.lease
        harness = self._resolve_harness(via="unmet-produces-resume")
        if harness is None:
            return
        self.ctx.usage.record_worker(lease, bindings)
        # The nudge already opened its own boundary at the call site — `_wake` self-
        # determines this and skips opening a second one, even for a LATER, unrelated wake.
        pid, _ = self._wake(message, bindings, harness=harness)
        _log.info(
            "resumed premature exit for unmet produces",
            chunk_id=lease.chunk_id,
            lease_id=lease.lease_id,
            epoch=lease.epoch,
            pid=pid,
        )

    def park_on_ask(self, ask: OpenAsk) -> None:
        """Park the chunk on a question: forward it to the hub and stop the reap clock; env
        bindings stay held so the session is warm for the resume. Only the usage record needs
        a harness, so an unresolvable one skips that record and nothing else."""
        lease = self.lease
        now = self.ctx.clock.now()
        bindings = self.ctx.stores.environments.bindings_for_chunk(lease.chunk_id)
        if self._resolve_harness_for_usage_only(via="park-on-ask-usage") is not None:
            self.ctx.usage.record_worker(lease, bindings)
        OutboundFacts(self.ctx).question_asked(lease, ask, at=now)
        self.ctx.stores.asks.record_park(
            lease_id=lease.lease_id, chunk_id=lease.chunk_id, question_id=ask.question_id, parked_at=now
        )
        if self.ctx.events is not None:
            # LeaseActivity.state flips to "parked" — see LeaseChangeCause's own doc
            # (wire/sse_runner.py) for why this cause isn't record_closure's "parked".
            self.ctx.events.publish_lease_changed(
                lease.lease_id,
                lease.chunk_id,
                cause="dormant",
            )
        _log.info("chunk parked on question", chunk_id=lease.chunk_id, question_id=ask.question_id)

    def restart_or_release(self, fenced: Fenced) -> None:
        """Park a paused chunk, else preempt a lease a restart fenced out while the runner was
        down, else resume in place, else abandon it if the hub reassigned its chunk, or if the
        hub no longer knows it at all (:func:`restart_disposition`)."""
        lease = self.lease
        try:
            view = self.ctx.chunk_views.get(lease.chunk_id)
        except ChunkNotFoundError:
            # The chunk is gone outright (e.g. a store reset) — terminal, not retryable; abandon
            # now rather than leave the intent open for PULL's lease reconcile to find later.
            Attempt(self.ctx, lease).abandon(via="resume")
            return
        except HubClientError:
            # Hub unreachable — the intent is durable and the envs stay held. Resuming blind
            # would risk re-asserting authority over a chunk that may have been reassigned.
            return
        runner_id = registered_runner_id(self.ctx.identity)
        if runner_id is None:
            return  # whether the route is still ours waits for the first registration; the intent is durable
        disposition = restart_disposition(view, runner_id, fenced=fenced.out(view, lease))
        if disposition is RestartDisposition.PARK:
            Attempt(self.ctx, lease).park_paused(via="resume")
        elif disposition is RestartDisposition.PREEMPT:
            Attempt(self.ctx, lease).preempt(via="resume")
        elif disposition is RestartDisposition.RESTART:
            self._restart()
        else:
            Attempt(self.ctx, lease).abandon(via="resume")

    def on_answer(self) -> None:
        """Poll a parked lease's question; on an answer, resume the dormant session.

        Crash-safe and re-runnable: an unanswered question polls as a no-op and the reap clock
        stays stopped. Once answered the agent is reconstituted under the same session and step."""
        lease = self.lease
        if Spawner(self.ctx).suppressed(via="answer-resume", chunk_id=lease.chunk_id, lease_id=lease.lease_id):
            return
        park = self.ctx.stores.asks.open_park(lease.lease_id)
        if park is None:
            return  # not actually parked (raced with a resume)
        try:
            question = self.ctx.hub.get_question(park.question_id)
        except HubClientError:
            return  # hub unreachable — the park is durable; retry next tick
        if not answer_ready(question):
            return  # still waiting — reap clock stays stopped
        bindings = self.ctx.stores.environments.bindings_for_chunk(lease.chunk_id)
        if not bindings:
            _log.warning("answered park with no bound env — cannot resume", chunk_id=lease.chunk_id)
            return
        harness = self._resolve_harness(via="answer-resume")
        if harness is None:
            return
        # The human framing rides a leading `#` comment line and the answer itself is the
        # payload; the exact prose is unpinned.
        who = question.answered_by or "operator"
        pid, now = self._wake(f"# Answer from {who}. Continue.\n{question.answer}", bindings, harness=harness)
        self.ctx.stores.asks.record_park_resume(lease_id=lease.lease_id, question_id=park.question_id, resumed_at=now)
        if self.ctx.events is not None:
            self.ctx.events.publish_ask_changed(
                lease.lease_id,
                lease.chunk_id,
                park.question_id,
                cause="answered",
            )
        OutboundFacts(self.ctx).answer_delivered(lease, park.question_id, at=now)
        _log.info("resumed dormant session with answer", chunk_id=lease.chunk_id, question_id=park.question_id, pid=pid)

    def on_unpause(self, park: PausePark, elicitation: PendingElicitation | None) -> None:
        """Finish a pause park's teardown, then poll its chunk; once the operator resumes it, restart
        its session. The teardown runs ahead of every read and move below,
        since a kill is not a spawn and the interrupted envelope is owed its recording regardless.
        The pause cost the chunk a process, not an attempt; an **ask-parked** lease
        returns early even once unpaused, so a lift never conjures an absent answer.
        ``elicitation`` is the tick's hoisted read (``bzh:bulk-reconstitution``) of this lease's
        in-flight elicitation, if any — the settled-check's own use of it below; the later,
        far rarer resume-time check reads fresh, since settling may have just cleared it."""
        lease = self.lease
        if not self._pause_park_settled(park, elicitation):
            return
        try:
            view = self.ctx.chunk_views.get(lease.chunk_id)
        except ChunkNotFoundError:
            # The chunk is gone outright — not this step's abandon to make; the reconcile sweep
            # owns it and runs ahead of this step in the same tick.
            return
        except HubClientError:
            return  # hub unreachable — the park is durable; retry next tick
        now = self.ctx.clock.now()
        # Read fresh, not the tick's hoisted read: settling above may have just cleared it.
        standing = self.ctx.stores.elicitations.in_flight_elicitation(lease.lease_id, lease.epoch)
        bindings = self.ctx.stores.environments.bindings_for_chunk(lease.chunk_id)
        runner_id = registered_runner_id(self.ctx.identity)
        if runner_id is None:
            return  # stays parked: whether the route is still ours waits for the first registration
        move = unpause_move(
            view,
            runner_id,
            braked=Spawner(self.ctx).suppressed(via="pause-resume", chunk_id=lease.chunk_id, lease_id=lease.lease_id),
            ask_parked=lease.lease_id in self.ctx.stores.asks.ask_parked_lease_ids(),
            judge_parked=standing is not None,
            has_env_and_session=bool(bindings) and lease.session is not None,
        )
        if move is UnpauseMove.WAIT:
            return  # still paused, braked, or detached while parked — PULL's sweep abandons it, not this step
        if move is UnpauseMove.CLEAR_AWAIT_ANSWER:
            # Dormant on a question underneath the pause: clearing the pause-park is the whole
            # action, and an answer — not this resume — restarts it.
            self.ctx.stores.pause.record_pause_park_resume(lease_id=lease.lease_id, resumed_at=now)
            _log.info("pause lifted on an ask-parked chunk — awaiting its answer", chunk_id=lease.chunk_id)
            return
        if move is UnpauseMove.RELAUNCH_JUDGE and standing is not None:
            self._resume_judge_usage_limit_park(now, superseded_invocation=iso_utc(standing.first_launched_at))
            return
        if move is UnpauseMove.CANNOT_RESUME:
            _log.warning("unpaused chunk has no warm env/session — cannot resume", chunk_id=lease.chunk_id)
            return
        harness = self._resolve_harness(via="unpause-resume")
        if harness is None:
            return
        # The paused generation's own usage — recorded before `_wake` mints the new one.
        self.ctx.usage.record_worker(lease, bindings)
        pid, _ = self._wake(_UNPAUSE_MESSAGE, bindings, harness=harness, at=now)
        self.ctx.stores.pause.record_pause_park_resume(lease_id=lease.lease_id, resumed_at=now)
        _log.info(
            "resumed dormant session after an operator unpause",
            chunk_id=lease.chunk_id,
            lease_id=lease.lease_id,
            epoch=lease.epoch,
            pid=pid,
        )

    def _pause_park_settled(self, park: PausePark, elicitation: PendingElicitation | None) -> bool:
        """True once nothing of the lease's is alive after `park_paused`'s interrupt — the worker's
        group and the elicitation the park names. Alive within the drain budget of
        ``parked_at``: left alone, no wake. Past it: SIGKILLed. A named elicitation that has exited
        books its ``judge`` usage, then clears — ``Judgement.collect``'s own order, both replays
        idempotent. An unnamed standing record is a usage-limit judge park's, left for its relaunch.
        ``elicitation`` is the tick's hoisted read of this lease's in-flight elicitation, if any."""
        lease = self.lease
        past_deadline = pause_park_drain_expired(park, now=self.ctx.clock.now())
        if not self._owned_group_settled(
            pid=lease.pid, process_start_time=lease.process_start_time, pgid=lease.pgid, past_deadline=past_deadline
        ):
            return False
        if not park_names_elicitation(park, elicitation) or elicitation is None:
            return True
        if not self._owned_group_settled(
            pid=elicitation.pid,
            process_start_time=elicitation.process_start_time,
            pgid=elicitation.pgid,
            past_deadline=past_deadline,
        ):
            return False
        bindings = self.ctx.stores.environments.bindings_for_chunk(lease.chunk_id)
        if self._resolve_harness_for_usage_only(via="pause-park-elicitation-usage") is not None:
            output = self.ctx.elicitation_files.read(elicitation.output_path)
            self.ctx.usage.record_attempt(lease, bindings, judge_output=output)
        self.ctx.stores.elicitations.clear_elicitation(lease.lease_id, lease.epoch)
        self.ctx.elicitation_files.cleanup(lease.lease_id, lease.epoch, through_attempt=elicitation.relaunch_count)
        _log.info(
            "interrupted elicitation recorded and cleared under the pause park",
            chunk_id=lease.chunk_id,
            lease_id=lease.lease_id,
            epoch=lease.epoch,
        )
        return True

    def _owned_group_settled(
        self, *, pid: int | None, process_start_time: str | None, pgid: int | None, past_deadline: bool
    ) -> bool:
        """True once this owned process and its group are gone. Alive within the budget:
        left alone. Alive past it: SIGKILLed through the shared liveness-checked kill, and
        settled once confirmed gone by the same LEADER-identity check — except when the
        leader already read dead going in, so only the identity-unguarded ``group_alive``
        was keeping this alive: past the kill, that alone never blocks settlement, the same
        fire-and-forget posture :class:`~blizzard.runner.lifecycle.shutdown_drain.ShutdownDrain`
        takes with its own survivors — a stray descendant the kill missed, or a pgid reused
        for something else entirely, both read alike, and neither may hold resume forever."""
        if pid is None or process_start_time is None:
            return True
        process = self.ctx.process
        if not owned_process_alive(process, pid=pid, process_start_time=process_start_time, pgid=pgid):
            return True
        if not past_deadline:
            return False
        leader_alive = process.is_alive(pid, process_start_time)
        kill_owned_process(process, pid=pid, process_start_time=process_start_time, pgid=pgid)
        _log.warning(
            "paused process outlived the interrupt budget — killed",
            chunk_id=self.lease.chunk_id,
            lease_id=self.lease.lease_id,
            pid=pid,
        )
        return True if not leader_alive else not process.is_alive(pid, process_start_time)

    def on_overload_backoff(self, fact: OverloadExit) -> None:
        """No-op until ``fact.resume_after`` has passed, then resume the same
        lease/epoch/session in place — no retry consumed, no epoch bumped.

        Nothing is written on the no-op branch: ``resume_after`` is already durable, and
        `backing_off_facts` re-derives the same fact next tick. A worker generation wakes
        via `_wake` directly; a judge elicitation gets a fresh launch instead, mirroring
        :meth:`_resume_judge_usage_limit_park` — its own turn already finished normally
        before the elicitation overloaded, so there is nothing to "continue" by message."""
        lease = self.lease
        now = self.ctx.clock.now()
        if not fact.due(now):
            return
        suppressed = Spawner(self.ctx).suppressed(
            via="overload-backoff-resume", chunk_id=lease.chunk_id, lease_id=lease.lease_id
        )
        if suppressed:
            return
        if fact.invocation_kind == "judge":
            self._resume_judge_overload_backoff(now, superseded_invocation=fact.invocation_identity)
            return
        bindings = self.ctx.stores.environments.bindings_for_chunk(lease.chunk_id)
        if not bindings or lease.session is None:
            _log.warning("backing-off chunk has no warm env/session — cannot resume", chunk_id=lease.chunk_id)
            return
        harness = self._resolve_harness(via="overload-backoff-resume")
        if harness is None:
            return
        # The overloaded generation's own usage — recorded before `_wake` mints the new one.
        self.ctx.usage.record_worker(lease, bindings)
        pid, _ = self._wake(_OVERLOAD_BACKOFF_MESSAGE, bindings, harness=harness, at=now)
        _log.info(
            "resumed a worker generation after a provider-overload backoff",
            chunk_id=lease.chunk_id,
            lease_id=lease.lease_id,
            epoch=lease.epoch,
            pid=pid,
        )

    def _resume_judge_overload_backoff(self, now: datetime, *, superseded_invocation: str) -> None:
        """The judge half of :meth:`on_overload_backoff` — clear-then-relaunch, shaped like
        :meth:`_resume_judge_usage_limit_park`: `Judgement._launch`'s `record_elicitation_launch`
        deletes-then-inserts over the stale record, so one always exists. The resume goes straight to a
        fresh launch (`Judgement.resume`): the gate and the produces nudge already ran on this exit."""
        lease = self.lease
        # Deferred: `judgement` imports `DormantSession` at module scope, so importing
        # `Judgement` back at module scope here would cycle.
        from blizzard.runner.lifecycle.judgement.judgement import Judgement

        judgement = Judgement.of(self.ctx, lease)
        if judgement is None:
            return  # hub unreachable — `resume_after` is durable; retry next tick
        if lease.session is not None:
            # The standing "judge" boundary is reused, not reopened (`record_boundary_open`'s
            # check-then-insert never mints a second row for one (lease, generation, kind)) —
            # record an advance past the overloaded elicitation's own signal, or the fresh one's own
            # classification would re-read that same signal off the transcript forever. Keyed
            # by the superseded elicitation, so a replay while it still stands writes nothing.
            generation = self.ctx.stores.liveness.lease_generation(lease.lease_id)
            workdir = judgement.bindings[0].workdir if judgement.bindings else None
            start_position, start_unreadable = self.ctx.resolve_boundary_start(lease.session, workdir)
            self.ctx.stores.invocation_boundaries.record_boundary_advance(
                lease_id=lease.lease_id,
                generation=generation,
                kind="judge",
                superseded_invocation=superseded_invocation,
                start_position=start_position,
                start_unreadable=start_unreadable,
                advanced_at=now,
            )
        judgement.resume()
        _log.info(
            "resumed a judge elicitation after a provider-overload backoff",
            chunk_id=lease.chunk_id,
            lease_id=lease.lease_id,
            epoch=lease.epoch,
        )

    def _resume_judge_usage_limit_park(self, now: datetime, *, superseded_invocation: str) -> None:
        """Unpause a judge-usage-limit park: the worker's own turn already
        finished normally before its verdict elicitation hit the limit, so there is nothing
        left to "continue" — re-running `Judgement` mints a fresh elicitation instead of
        waking the worker with `_UNPAUSE_MESSAGE`, straight to a launch (`Judgement.resume`) since
        the gate and the produces nudge already ran on this exit. The stale record from the
        limited elicitation is left for `Judgement._launch`'s own `record_elicitation_launch`
        to delete-then-insert over, rather than cleared here first — no window where neither
        record exists. The park-resume is recorded only AFTER the fresh elicitation is
        durably launched: if a crash lands between them, the lease is still pause-parked, so
        the next pass re-enters this same method rather than the ordinary exited-worker path
        re-classifying the same stale record and re-engaging the brake it was just cleared
        from."""
        lease = self.lease
        # Deferred: `judgement` imports `DormantSession` at module scope, so importing
        # `Judgement` back at module scope here would cycle.
        from blizzard.runner.lifecycle.judgement.judgement import Judgement

        judgement = Judgement.of(self.ctx, lease)
        if judgement is None:
            return  # hub unreachable — the park is durable; retry next tick
        if lease.session is not None:
            # The standing "judge" boundary is reused, not reopened (`record_boundary_open`'s
            # check-then-insert never mints a second row for one (lease, generation, kind)) —
            # record an advance past the limited elicitation's own signal, or the fresh one's own
            # classification would re-read that same signal off the transcript forever. Keyed
            # by the superseded elicitation, so a replay while it still stands writes nothing.
            generation = self.ctx.stores.liveness.lease_generation(lease.lease_id)
            workdir = judgement.bindings[0].workdir if judgement.bindings else None
            start_position, start_unreadable = self.ctx.resolve_boundary_start(lease.session, workdir)
            self.ctx.stores.invocation_boundaries.record_boundary_advance(
                lease_id=lease.lease_id,
                generation=generation,
                kind="judge",
                superseded_invocation=superseded_invocation,
                start_position=start_position,
                start_unreadable=start_unreadable,
                advanced_at=now,
            )
        judgement.resume()
        self.ctx.stores.pause.record_pause_park_resume(lease_id=lease.lease_id, resumed_at=now)
        _log.info(
            "resumed a judge-usage-limit park with a fresh elicitation",
            chunk_id=lease.chunk_id,
            lease_id=lease.lease_id,
            epoch=lease.epoch,
        )

    def _restart(self) -> None:
        """Kill any survivor, then resume the session under the same lease/epoch/session.

        Kill-first is what prevents two processes on one session — the epoch is not. The brake is
        checked **before the kill**: gating after would kill the survivor and leave it
        un-re-attached."""
        lease = self.lease
        if Spawner(self.ctx).suppressed(via="resume", chunk_id=lease.chunk_id, lease_id=lease.lease_id):
            return
        harness: IHarnessWorkerLifecycle | None = None
        if lease.session is not None:
            harness = self._resolve_harness(via="restart-resume")
            if harness is None:
                # `_resolve_harness` already escalated and killed any survivor as part of that
                # closure — no other runner can resume this exact session to re-attach here.
                return
        now = self.ctx.clock.now()
        # Kill-first — never two processes on one session — via the shared, liveness-checked
        # kill: a stale/reused pid is never blindly signaled.
        kill_owned_process(
            self.ctx.process, pid=lease.pid, process_start_time=lease.process_start_time, pgid=lease.pgid
        )
        _CP_RESUME_AFTER_KILL.reached()  # re-run kills the dead pid (no-op) then re-attaches
        bindings = self.ctx.stores.environments.bindings_for_chunk(lease.chunk_id)
        if not bindings or lease.session is None or harness is None:
            _log.warning(
                "marked lease has no warm env/session — abandoning", chunk_id=lease.chunk_id, lease_id=lease.lease_id
            )
            Attempt(self.ctx, lease).abandon(killed=True, via="resume")
            return
        # The crashed generation's own usage — recorded before `_wake` mints the new one.
        self.ctx.usage.record_worker(lease, bindings)
        pid, _ = self._wake(_RESTART_MESSAGE, bindings, harness=harness, at=now)
        self.ctx.stores.resume_intent.record_resume_clear(lease_id=lease.lease_id, cleared_at=now)
        _CP_RESUME_AFTER.reached()  # pid recorded, intent cleared — a crash here re-runs as a no-op
        _log.info(
            "resumed in-flight session after restart",
            chunk_id=lease.chunk_id,
            lease_id=lease.lease_id,
            epoch=lease.epoch,
            pid=pid,
        )

    def _resolve_harness(self, *, via: str) -> IHarnessWorkerLifecycle | None:
        """Resolve the dormant session's recorded owner; escalate the chunk in place via
        :meth:`Attempt.escalate_owner_unresolvable` and return ``None`` — never raising —
        when it is unknown or unavailable. :meth:`park_on_ask` uses
        :meth:`_resolve_harness_for_usage_only` instead, since parking needs no harness at all."""
        lease = self.lease
        session = lease.session
        assert session is not None
        try:
            return self.ctx.harnesses.lifecycle(session.harness_id)
        except (UnknownHarnessError, UnavailableHarnessError) as exc:
            Attempt(self.ctx, lease).escalate_owner_unresolvable(session=session, exc=exc, via=via)
            return None

    def _resolve_harness_for_usage_only(self, *, via: str) -> IHarnessWorkerLifecycle | None:
        """Resolve the dormant session's recorded owner, logging and returning ``None`` —
        never escalating, never raising — when it is unknown or unavailable."""
        lease = self.lease
        session = lease.session
        assert session is not None
        try:
            return self.ctx.harnesses.lifecycle(session.harness_id)
        except (UnknownHarnessError, UnavailableHarnessError) as exc:
            _log.error(
                "dormant session usage record blocked by unavailable harness owner",
                via=via,
                chunk_id=lease.chunk_id,
                lease_id=lease.lease_id,
                harness_id=session.harness_id,
                detail=str(exc),
            )
            return None

    def _worker_boundary_already_open(self, generation: int) -> bool:
        """Whether some worker-starting kind (:data:`WORKER_STARTING_KINDS`) already opened
        its boundary at this generation — `_wake`'s own, self-determined guard against opening
        a second one alongside a nudge's own, even across a LATER, unrelated wake trigger
        reaching the same still-dormant lease."""
        return worker_boundary_open(
            self.ctx.stores.invocation_boundaries.open_boundaries_for_lease(self.lease.lease_id), generation
        )

    def _wake(
        self,
        message: str,
        bindings: list[EnvBinding],
        *,
        harness: IHarnessWorkerLifecycle,
        at: datetime | None = None,
    ) -> tuple[int, datetime]:
        """Deliver ``message`` into the dormant session and record the new pid under the same
        lease, returning that pid with the instant it was stamped — an omitted ``at`` reads the
        clock *after* the resume returns. ``harness`` is the caller's already-resolved owner, so
        this method can never be reached with an unresolvable one. Opens a `resume` boundary
        unless :meth:`_worker_boundary_already_open` finds one open already."""
        lease = self.lease
        spawner = Spawner(self.ctx)
        session = lease.session
        assert session is not None
        generation = spawner.generation(lease.lease_id)
        if not self._worker_boundary_already_open(generation):
            workdir = bindings[0].workdir if bindings else None
            start_position, start_unreadable = self.ctx.resolve_boundary_start(session, workdir)
            self.ctx.stores.invocation_boundaries.record_boundary_open(
                lease_id=lease.lease_id,
                chunk_id=lease.chunk_id,
                node_id=lease.node_id,
                epoch=lease.epoch,
                generation=generation,
                kind="resume",
                start_position=start_position,
                start_unreadable=start_unreadable,
                opened_at=at if at is not None else self.ctx.clock.now(),
            )
            _CP_WAKE_AFTER_BOUNDARY.reached()
        # Observed BEFORE the resume, the same as a fresh spawn (lifecycle/spawn.py): a hung or
        # failing probe must never run after the worker is already live and unrecorded.
        version = harness.observe_version()
        spawn_cwd = SpawnCwd.of_session(self.ctx.config.workspace_root, bindings[0].workdir)
        resumed = harness.resume_with_message(
            spawn_cwd,
            session.session_id,
            message,
            stdout_path=spawner.stdout_path(lease.lease_id),
            preamble=spawner.preamble(lease, bindings),
            chunk_id=lease.chunk_id,
            model=lease.resolved_model,
            # Reasserted, not sticky — see the judge call site's note.
            effort=lease.resolved_effort,
            # Reasserted, not sticky either — mirrors effort's treatment.
            compaction_window=lease.resolved_compaction_window,
        )
        _CP_WAKE_AFTER_LAUNCH.reached()  # the process exists; nothing about it is durable yet
        stamped = at if at is not None else self.ctx.clock.now()
        assert lease.session is not None
        try:
            self.ctx.stores.liveness.record_spawn(
                lease.lease_id,
                pid=resumed.pid,
                # The launcher's own recorded start time — never re-probed a second
                # time here, which would race a pid-reuse window opening after it.
                process_start_time=resumed.process_start_time,
                # The launcher's own recorded group — carried through, never inferred
                # as `pgid=pid` at this call site.
                pgid=resumed.pgid,
                session=lease.session,  # unchanged — same concrete session under the same lease
                spawned_at=stamped,
                harness_version=version,
                spawn_cwd=spawn_cwd,
            )
        except Exception:
            # A plain raise here never disarms the trampoline on its own — kill it
            # explicitly instead (`Spawner.spawn`'s own guard, mirrored here).
            self.ctx.process.kill_group(resumed.pgid)
            raise
        _CP_WAKE_AFTER_RECORD.reached()  # ownership durable; not yet disarmed
        # Disarm only now this record is durable — a later REAP/ADVANCE pass can
        # re-adopt this exact process past here.
        resumed.confirm_durable()
        if self.ctx.events is not None:
            # Same 'spawned' cause the fresh-spawn path publishes (lifecycle/spawn.py) — a resumed
            # session's own flip back to a live pid is exactly as un-announced otherwise.
            self.ctx.events.publish_lease_changed(
                lease.lease_id,
                lease.chunk_id,
                cause="spawned",
            )
        return resumed.pid, stamped
