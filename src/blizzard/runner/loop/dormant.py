"""A lease whose worker is gone but whose session survives — parking into it, and waking it."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.crash import crashpoint
from blizzard.foundation.logging import get_logger
from blizzard.runner.domain.asks import AskRecord
from blizzard.runner.domain.leases import LeaseRecord
from blizzard.runner.environments.repository import EnvBindingRecord
from blizzard.runner.harness.adapter import IHarnessLifecycleAndVerdict
from blizzard.runner.harness.registry import UnavailableHarnessError, UnknownHarnessError
from blizzard.runner.loop.attempt import Attempt
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.hub import ChunkNotFoundError, HubClientError
from blizzard.runner.loop.outbound import OutboundFacts
from blizzard.runner.loop.process import kill_owned_process
from blizzard.runner.loop.spawn import Spawner

_log = get_logger("blizzard.runner.loop")

#: The message RESUME delivers into a marked session on a restart — ``#``-prefixed so it is
#: inert in prose and in a behavior script alike. The exact prose is unpinned.
_RESTART_MESSAGE = "# The supervisor restarted; continue your task where you left off."

#: The message ADVANCE delivers into a session the operator paused and resumed (issue #46).
#: Same inert ``#``-prefixed framing; the exact prose is unpinned.
_UNPAUSE_MESSAGE = "# The operator resumed this chunk; continue your task where you left off."

# The restart re-attach. `_wake`'s own middle (a resumed process launched but not yet
# durably recorded) is armed exactly like SPAWN's two-phase mint (D1/D4), below; recovery
# re-runs RESUME idempotently regardless of which of these four windows a crash lands in.
_CP_RESUME_AFTER_KILL = crashpoint("resume.after-kill.before-reattach", "survivor killed; session not yet re-attached")
_CP_RESUME_AFTER = crashpoint("resume.after-reattach", "session re-attached under the same lease; intent cleared")
_CP_WAKE_AFTER_LAUNCH = crashpoint(
    "resume.wake.after-launch.before-record", "resumed process exists; pid not yet durable"
)
_CP_WAKE_AFTER_RECORD = crashpoint("resume.wake.after-record", "resumed pid durably recorded; launch not yet disarmed")


@dataclass(frozen=True)
class DormantSession:
    """A lease with no live worker but a session still resumable under it — parked on a
    question, parked on an operator pause, or marked for restart-resume.

    Every wake here rewrites only ``pid``/``process_start_time``: same lease, same epoch, same
    session, so **no retry is consumed** by going dormant and coming back."""

    ctx: LoopContext
    lease: LeaseRecord

    def resume_on_unmet_produces(self, message: str, bindings: list[EnvBindingRecord]) -> None:
        """Resume a session that exited with required ``produces:`` unattached, instead of
        judging it (issue #422) — no retry consumed, no epoch bumped. Owner resolution is
        checked **before** the generation's spend is recorded (the same "resolve before any
        mutation" shape :meth:`_restart` uses), so an unresolvable owner escalates in place
        rather than recording a spend for a wake that never happens."""
        lease = self.lease
        harness = self._resolve_harness(via="unmet-produces-resume")
        if harness is None:
            return
        self.ctx.usage.record_worker(lease, bindings)
        pid, _ = self._wake(message, bindings, harness=harness)
        _log.info(
            "resumed premature exit for unmet produces",
            chunk_id=lease.chunk_id,
            lease_id=lease.lease_id,
            epoch=lease.epoch,
            pid=pid,
        )

    def park_on_ask(self, ask: AskRecord) -> None:
        """Park the chunk on a question: forward it to the hub and stop the reap clock; env
        bindings stay held so the session is warm for the resume. Parking itself needs no
        harness — only the generation's usage spend does, so an unresolvable owner there just
        skips that record, logged rather than escalated, since nothing here resumes a session
        to leave half-done."""
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
            # LeaseActivity.state (D4) flips to "parked" — see LeaseChangeCause's own doc
            # (wire/sse_runner.py) for why this cause isn't record_closure's "parked".
            self.ctx.events.publish_lease_changed(
                lease.lease_id,
                lease.chunk_id,
                cause="dormant",
            )
        _log.info("chunk parked on question", chunk_id=lease.chunk_id, question_id=ask.question_id)

    def restart_or_release(self) -> None:
        """Park a paused chunk, else resume in place, else abandon it if the hub reassigned its
        chunk (issue #46), or if the hub no longer knows it at all (blizzard#9).

        The pause branch is **first** and keys on the pause *fact*, not the lossy derived status.
        It is conjoined with ``ours``, so a detached-then-paused chunk still abandons."""
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
        ours = view.route_runner_id == self.ctx.config.runner_id
        if ours and view.pause is not None:
            Attempt(self.ctx, lease).park_paused(via="resume")
        elif view.status == ChunkStatus.RUNNING and ours:
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
        if not question.answered or question.answer is None:
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

    def on_unpause(self) -> None:
        """Poll a pause-parked lease's chunk; once the operator resumes it, restart its session.

        The pause cost the chunk a process, not an attempt. An **ask-parked** lease returns early
        even once unpaused, so a lift never conjures an absent answer (issue #46)."""
        lease = self.lease
        if Spawner(self.ctx).suppressed(via="pause-resume", chunk_id=lease.chunk_id, lease_id=lease.lease_id):
            return
        try:
            view = self.ctx.chunk_views.get(lease.chunk_id)
        except ChunkNotFoundError:
            # The chunk is gone outright — not this step's abandon to make; the reconcile sweep
            # owns it and runs ahead of this step in the same tick.
            return
        except HubClientError:
            return  # hub unreachable — the park is durable; retry next tick
        if view.pause is not None:
            return  # still paused — the reap clock stays stopped
        if view.route_runner_id != self.ctx.config.runner_id:
            return  # detached/reassigned while parked — PULL's sweep abandons it, not this step
        now = self.ctx.clock.now()
        if lease.lease_id in self.ctx.stores.asks.ask_parked_lease_ids():
            # Dormant on a question underneath the pause: clearing the pause-park is the whole
            # action, and an answer — not this resume — restarts it.
            self.ctx.stores.pause.record_pause_park_resume(lease_id=lease.lease_id, resumed_at=now)
            _log.info("pause lifted on an ask-parked chunk — awaiting its answer", chunk_id=lease.chunk_id)
            return
        bindings = self.ctx.stores.environments.bindings_for_chunk(lease.chunk_id)
        if not bindings or lease.session is None:
            _log.warning("unpaused chunk has no warm env/session — cannot resume", chunk_id=lease.chunk_id)
            return
        harness = self._resolve_harness(via="unpause-resume")
        if harness is None:
            return
        pid, _ = self._wake(_UNPAUSE_MESSAGE, bindings, harness=harness, at=now)
        self.ctx.stores.pause.record_pause_park_resume(lease_id=lease.lease_id, resumed_at=now)
        _log.info(
            "resumed dormant session after an operator unpause",
            chunk_id=lease.chunk_id,
            lease_id=lease.lease_id,
            epoch=lease.epoch,
            pid=pid,
        )

    def _restart(self) -> None:
        """Kill any survivor, then resume the session under the same lease/epoch/session.

        Kill-first is what prevents two processes on one session — the epoch is not. The brake is
        checked **before the kill**: gating after would kill the survivor and leave it
        un-re-attached."""
        lease = self.lease
        if Spawner(self.ctx).suppressed(via="resume", chunk_id=lease.chunk_id, lease_id=lease.lease_id):
            return
        harness: IHarnessLifecycleAndVerdict | None = None
        if lease.session is not None:
            harness = self._resolve_harness(via="restart-resume")
            if harness is None:
                # `_resolve_harness` already escalated and killed any survivor as part of that
                # closure — no other runner can resume this exact session to re-attach here.
                return
        now = self.ctx.clock.now()
        # Kill-first — never two processes on one session — via the shared, liveness-checked
        # kill (D3): a stale/reused pid is never blindly signaled.
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

    def _resolve_harness(self, *, via: str) -> IHarnessLifecycleAndVerdict | None:
        """Resolve the dormant session's recorded owner; escalate the chunk in place and
        return ``None`` — never raising — when it is unknown or unavailable, the guard every
        wake path shares. No other runner can resume this exact session, so escalating is the
        only move that does not substitute a different harness. :meth:`park_on_ask` uses
        :meth:`_resolve_harness_for_usage_only` instead, since parking needs no harness at all."""
        lease = self.lease
        session = lease.session
        assert session is not None
        try:
            return self.ctx.adapter_for(session)
        except (UnknownHarnessError, UnavailableHarnessError) as exc:
            Attempt(self.ctx, lease).escalate_owner_unresolvable(session=session, exc=exc, via=via)
            return None

    def _resolve_harness_for_usage_only(self, *, via: str) -> IHarnessLifecycleAndVerdict | None:
        """:meth:`park_on_ask`'s own guard: log and return ``None`` — never escalate, never
        raise — when the recorded owner is unknown or unavailable. Parking itself needs no
        harness, so this skips only the harness-dependent usage record it gates, the same
        shape :meth:`_resolve_harness` had before it started escalating."""
        lease = self.lease
        session = lease.session
        assert session is not None
        try:
            return self.ctx.adapter_for(session)
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

    def _wake(
        self,
        message: str,
        bindings: list[EnvBindingRecord],
        *,
        harness: IHarnessLifecycleAndVerdict,
        at: datetime | None = None,
    ) -> tuple[int, datetime]:
        """Deliver ``message`` into the dormant session and record the new pid under the same
        lease, returning that pid with the instant it was stamped — an omitted ``at`` reads the
        clock *after* the resume returns. ``harness`` is the caller's already-resolved owner
        (:meth:`_resolve_harness`), so this method can never be reached with an unresolvable one.
        The resume → ``record_spawn`` gap is armed exactly like a fresh spawn or judge launch (D1/D4)."""
        lease = self.lease
        spawner = Spawner(self.ctx)
        session = lease.session
        assert session is not None
        # Observed BEFORE the resume, the same as a fresh spawn (spawn.py): a hung or
        # failing probe must never run after the worker is already live and unrecorded.
        version = harness.observe_version()
        resumed = harness.resume_with_message(
            bindings[0].workdir,
            session.session_id,
            message,
            stdout_path=spawner.stdout_path(lease.lease_id),
            preamble=spawner.preamble(lease, bindings),
            chunk_id=lease.chunk_id,
            # Reasserted, not sticky (issue #144) — see the judge call site's note.
            effort=lease.resolved_effort,
            # Reasserted, not sticky either (blizzard#343) — mirrors effort's treatment.
            compaction_window=lease.resolved_compaction_window,
        )
        _CP_WAKE_AFTER_LAUNCH.reached()  # the process exists; nothing about it is durable yet
        stamped = at if at is not None else self.ctx.clock.now()
        assert lease.session is not None
        try:
            self.ctx.stores.liveness.record_spawn(
                lease.lease_id,
                pid=resumed.pid,
                # The launcher's own recorded start time (D3) — never re-probed a second
                # time here, which would race a pid-reuse window opening after it (F14).
                process_start_time=resumed.process_start_time,
                # The launcher's own recorded group (D3) — carried through, never inferred
                # as `pgid=pid` at this call site.
                pgid=resumed.pgid,
                session=lease.session,  # unchanged — same concrete session under the same lease
                spawned_at=stamped,
                harness_version=version,
            )
        except Exception:
            # F1: a plain raise here never disarms the trampoline on its own — kill it
            # explicitly instead (`Spawner.spawn`'s own guard, mirrored here).
            self.ctx.process.kill_group(resumed.pgid)
            raise
        _CP_WAKE_AFTER_RECORD.reached()  # ownership durable; not yet disarmed
        # F1: disarm only now this record is durable — a later REAP/ADVANCE pass can
        # re-adopt this exact process past here.
        resumed.confirm_durable()
        if self.ctx.events is not None:
            # Same 'spawned' cause the fresh-spawn path publishes (spawn.py) — a resumed
            # session's own flip back to a live pid is exactly as un-announced otherwise.
            self.ctx.events.publish_lease_changed(
                lease.lease_id,
                lease.chunk_id,
                cause="spawned",
            )
        return resumed.pid, stamped
