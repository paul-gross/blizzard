"""The reconciliation step functions — REAP → PULL → FILL → ADVANCE (``bzh:steppable-loop``).

Each is an individually callable function of a :class:`LoopContext`; the tick driver
and the ``blizzard runner tick`` CLI verb call them in order. Every step is
idempotent and holds no state of its own — all facts live in the runner store, so a
crash mid-tick followed by a restart re-runs the tick harmlessly (D-023/D-028), and
startup recovery is just REAP running first.

The dead-worker split (design/runner/loop.md): a **session-bearing** worker whose
process has *exited* is a *done declaration* (exit-is-done, D-055) and belongs to
ADVANCE — its judgement reply, or its absence (D-009), tells a done from a crash.
REAP handles the residue ADVANCE structurally cannot judge: a lease whose worker
never reached spawn-return (no pid/session — killed mid-FILL), and a **stalled-but-
alive** worker whose heartbeat has gone stale (a live pid that stopped making tool
calls, so it stopped beating — D-069). Both the verdict-less-exit failure (ADVANCE)
and the reaped orphan/stall (REAP) route through one ``requeue-or-escalate`` decision
keyed on the node's retry budget (D-078/D-009). Liveness is heartbeat-freshness for a
live pid, plus (pid, start_time) to survive pid reuse.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from blizzard.foundation.crash import crashpoint
from blizzard.foundation.ids import LEASE_PREFIX, mint
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.domain.work import ChunkStatus
from blizzard.runner.domain.leases import as_utc, is_heartbeat_stale
from blizzard.runner.environments.provider import (
    AcquiredEnvironment,
    EnvironmentPreparationError,
    WorkspaceAcquisitionError,
)
from blizzard.runner.harness.adapter import WorkerPreamble
from blizzard.runner.harness.preamble import render_worker_preamble
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.hub import ChunkNotFoundError, HubClientError
from blizzard.runner.loop.process import IProcessProbe
from blizzard.runner.store.repository import (
    AskRecord,
    BufferedFact,
    EnvBindingRecord,
    IWriteRunnerStore,
    LeaseRecord,
    NewLease,
)
from blizzard.wire.completion import CompletionSubmission, SubmittedArtifact
from blizzard.wire.decision import DecisionSubmission, DecisionView
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse, NodeEnvelope
from blizzard.wire.facts import (
    ANSWER_DELIVERED,
    ESCALATION_RECORDED,
    LEASE_MINTED,
    QUESTION_ASKED,
    RunnerFact,
    RunnerFactBatch,
)
from blizzard.wire.route import RouteClaim

#: This module's public API — the loop steps it owns. ``HEARTBEAT_STALENESS_THRESHOLD``
#: lives in ``runner/domain/leases.py`` (its one owner, ``bzh:domain-core``); this
#: module no longer re-exports it — importers (tests included) reach it there.
__all__ = [
    "advance",
    "fill",
    "flush_outbound",
    "mark_resume_intents",
    "pull",
    "reap",
    "resume",
]

_log = get_logger("blizzard.runner.loop")

# Closure reasons (lease_closures.reason).
_TRANSITIONED = "transitioned"
_REAPED = "reaped"
_FAILED = "failed"
_ESCALATED = "escalated"
_PARKED = "parked"  # a runner-config gate: the node-step completed, the chunk parks on a decision
_RELEASED = "released"  # the chunk was found reassigned/detached/unknown — abandon, no requeue (D-088/blizzard#9)

#: The message RESUME delivers into a marked session on a restart (D-082). Framed
#: as a ``#``-prefixed comment so it is inert whether the session is real-harness prose or a
#: blizzard-mock behavior *script* it ``exec``s (the same convention as the elicitation tail
#: and the answer-resume framing). The exact prose is unpinned (D-061).
_RESTART_RESUME_MESSAGE = "# The supervisor restarted; continue your task where you left off."

# Outbound-buffer fact kinds (design/runner/store.md). ``completion.submitted`` is the
# runner-local kind whose flush drives the apply-response; ``decision.submitted`` is the
# runner-config gate's kind (D-032), which parks the chunk instead of advancing it; the
# two hub-fact kinds (LEASE_MINTED / ESCALATION_RECORDED) flush to POST /events (D-069/D-044).
_COMPLETION_KIND = "completion.submitted"
_DECISION_KIND = "decision.submitted"

# The env count a solo chunk wants; batching (K>1) is parked (design/runner/environments.md).
_SOLO_ENV_COUNT = 1

# --------------------------------------------------------------------------- #
# Crash points (``bzh:crash-point-registry``) — the runner tick's dangerous windows.
# Each is declared beside the boundary it guards and reached exactly there; armed, it
# SIGKILLs the tick subprocess so the kill-9 sweep exercises recovery from that instant.
# Unarmed, each is a no-op (a module-global string compare).
# --------------------------------------------------------------------------- #

# REAP — startup recovery runs this first, so these bracket the recovery pass itself.
_CP_REAP_BEFORE = crashpoint("reap.before-expire", "entered REAP; no lease expired yet")
_CP_REAP_AFTER = crashpoint("reap.after-expire", "REAP done; stale leases expired")

# RESUME — the restart re-attach, second in the tick (only ever non-empty on the first tick
# after a restart, graceful or crash-detected). These bracket the re-attach the way SPAWN brackets
# its spawn: the kill→re-attach→record window's *un-recordable* middle (the harness
# resume-with-message call whose pid is not yet durable) is the same by-construction gap
# SPAWN leaves between spawn and record_spawn — see ``_resume_in_place``. Armed at either
# bracket, recovery re-runs RESUME idempotently and the chunk still lands exactly once.
_CP_RESUME_BEFORE = crashpoint("resume.before-reattach", "entered RESUME with marked intents; none re-attached yet")
_CP_RESUME_AFTER_KILL = crashpoint("resume.after-kill.before-reattach", "survivor killed; session not yet re-attached")
_CP_RESUME_AFTER = crashpoint("resume.after-reattach", "session re-attached under the same lease; intent cleared")

# ABANDON — the reassigned/detached release (`_abandon_reassigned`), reached from RESUME (a chunk
# reassigned/detached while the runner was down), PULL's `_release_detached` (reassigned/detached
# while the runner was up, since D-088's live-tick detach), or REAP's `_fail_attempt` escalate
# guard (an exhausted-retries lease the hub already moved elsewhere, since blizzard#38). A crash
# here leaves a lease with a dead pid, environments not yet released, and no closure recorded, so
# the lease is still active at the next startup. That next tick's recovery differs by how the
# lease got here: a lease `mark_crash_resume_intents` marks for resume — session-bearing, not
# parked/pending-submission/session-ended, and not stale-heartbeat as measured at crash time —
# is re-asked by RESUME, finds it still not ours, and re-runs this same abandon idempotently. A
# lease in one of those skipped states gets no resume intent, so RESUME never revisits it — but
# PULL's own `_release_detached` re-scans *every* active lease each tick, unconditional on those
# states, and reaches the identical re-ask; it is the stronger recovery story of the two, and the
# one that actually covers every path into this function (killing an already-dead pid is a
# no-op; `_release_all` and `record_closure` are re-runnable), and the chunk lands exactly once.
_CP_ABANDON_AFTER_KILL = crashpoint(
    "abandon.after-kill.before-release", "detached worker killed; environments not yet released"
)

# PULL — the single outbound flusher (store-and-forward drain).
_CP_PULL_BEFORE = crashpoint("pull.before-flush", "entered PULL; registry synced, buffer not drained")
_CP_PULL_AFTER = crashpoint("pull.after-flush", "PULL done; buffer drained as far as it could")

# FILL — peek -> acquire -> BIND -> claim -> spawn (D-080/D-083). The local binding is
# written *before* the hub claim so it is the runner's durable anchor for a chunk it holds:
# a crash anywhere in the bind->claim->spawn window is reconciled next tick (adopt if the
# hub confirms the route is ours, else release the orphaned binding) — never a strand.
_CP_FILL_BEFORE_ACQUIRE = crashpoint("fill.before-env-acquire", "peeked a ready chunk; envs not acquired")
_CP_FILL_AFTER_ACQUIRE = crashpoint("fill.after-env-acquire.before-bind", "envs acquired; binding not recorded")
_CP_FILL_AFTER_BIND = crashpoint("fill.after-bind.before-claim", "binding recorded; route not claimed at the hub")
_CP_FILL_AFTER_CLAIM = crashpoint("fill.after-claim.before-spawn", "hub holds the route; lease not minted")

# SPAWN (shared by FILL's first spawn, ADVANCE's continue-in-place, and requeue): the
# lease-mint -> spawn -> record window is the orphan-lease window REAP must absorb.
_CP_SPAWN_AFTER_MINT = crashpoint("spawn.after-lease-mint.before-spawn", "lease minted; worker not spawned")
_CP_SPAWN_AFTER_SPAWN = crashpoint("spawn.after-spawn", "worker spawned; pid recorded")

# ADVANCE — judge an exited worker: push artifacts -> elicit verdict -> buffer completion.
_CP_ADV_BEFORE_PUSH = crashpoint("advance.before-artifact-push", "exited worker; artifacts not pushed")
_CP_ADV_AFTER_PUSH = crashpoint(
    "advance.after-artifact-push.before-judgement", "artifacts pushed; verdict not elicited"
)
_CP_ADV_AFTER_JUDGE = crashpoint("advance.after-judgement.before-buffer", "verdict parsed; completion not buffered")
_CP_ADV_AFTER_BUFFER = crashpoint("advance.after-buffer.before-flush", "completion buffered; not yet flushed")

# FLUSH (of the buffered completion, inside PULL) — submit -> ack -> apply-response. The
# after-submit.before-ack window is the lost-ack replay the hub's idempotency must absorb.
_CP_FLUSH_BEFORE_SUBMIT = crashpoint("flush.before-submit", "completion at head of buffer; not submitted")
_CP_FLUSH_AFTER_SUBMIT = crashpoint("flush.after-submit.before-ack", "hub applied the completion; ack not recorded")
_CP_FLUSH_AFTER_ACK = crashpoint("flush.after-ack.before-apply-response", "ack recorded; apply-response not consumed")
_CP_FLUSH_AFTER_APPLY = crashpoint("flush.after-apply-response", "apply-response consumed; chunk continued in place")


# --------------------------------------------------------------------------- #
# REAP
# --------------------------------------------------------------------------- #


def reap(ctx: LoopContext) -> None:
    """Expire leases whose worker is gone or **stalled** (design/runner/loop.md).

    Three cases end an attempt here (each a failed execution attempt, D-078 —
    requeue or escalate):

    * **orphan** — a lease with no recorded pid/session: minted at FILL but never
      spawned (a crash in the mint→spawn window). ADVANCE structurally cannot judge it.
    * **stalled-but-alive** — a live worker whose last heartbeat is older than the
      conservative :data:`HEARTBEAT_STALENESS_THRESHOLD`. Heartbeats ride tool calls
      (D-069), so a worker that stops progressing stops beating; there is no separate
      stall detector. REAP kills it (``_fail_attempt`` does the best-effort kill) — the
      epoch fence, not the kill, is what guarantees the zombie cannot deliver.

    A session-bearing worker whose process has **exited** is *not* reaped here: exit is
    the done declaration (D-055), so it belongs to ADVANCE, which resumes the session
    to tell a real completion from a crash. The conservative threshold is what keeps
    the two apart — a worker that exited cleanly still carries a fresh final heartbeat,
    so REAP never preempts ADVANCE's judgement of it.

    **The local brake (issue #45) is checked per case, not blanket, once the escalate
    branch grew its own gate.** The two live cases carry different stakes while locally
    paused:

    * the **stall** case has a live process to kill — the only kill in this function —
      and a local pause is not a drain (it must not kill a worker still running), so
      this case alone is suppressed here, deferred to the first tick after the brake
      clears.
    * the **orphan** case has no process to kill (``pid is None``, so the top-of-
      :func:`_fail_attempt` kill is a no-op) and its requeue branch already self-defers
      correctly — the respawn is gated at :func:`_spawn_attempt`, so no retry is consumed
      by construction (:data:`attempt_count` counts mints, and the mint sits below that
      gate) — and its escalate branch, at an exhausted budget, defers there too (the same
      gate every ``_fail_attempt`` caller shares). Suspending it here as well would only
      cost startup recovery time for no correctness gain, so it runs unguarded — at the
      price that its orphan leases occupy ``max_agents`` slots invisibly while paused,
      since FILL is paused too (logged below so that state is at least greppable).

    (An earlier version of this guard suspended both cases and justified it as "avoiding
    burning a retry on a brake" — false: the retry budget was never at risk, since it
    counts mints and every mint site already sits below :func:`_spawn_suppressed`. The
    real reason to suspend anything here is the kill, not the retry.)
    """
    _CP_REAP_BEFORE.reached()
    local_paused = ctx.store.local_paused(ctx.config.runner_id)
    now = ctx.clock.now()
    parked = ctx.store.parked_lease_ids()
    deferred = 0
    for lease in ctx.store.list_active_leases():
        if lease.lease_id in parked:
            # Dormant on a question (ask-and-exit): no live worker to stall, so the
            # reap clock is stopped — a parked chunk is never reaped for inactivity
            # ([ask-answer.md]). The answer's arrival resumes it (ADVANCE).
            continue
        if lease.pid is None or lease.session_id is None:
            _log.info("reaping unspawned lease", lease_id=lease.lease_id, chunk_id=lease.chunk_id)
            _fail_attempt(ctx, lease, reason=_REAPED, via="reap")
            continue
        if not ctx.process.is_alive(lease.pid, lease.process_start_time or ""):
            continue  # exited — ADVANCE's (exit-is-done, D-055)
        if is_heartbeat_stale(ctx.store, lease, now):
            if local_paused:
                # Do not kill a live worker while the runner's own brake is on — pause is
                # not a drain. The lease waits; the first tick after the brake clears
                # reaps it exactly as it would have now.
                deferred += 1
                continue
            _log.info("reaping stalled worker", lease_id=lease.lease_id, chunk_id=lease.chunk_id, pid=lease.pid)
            _fail_attempt(ctx, lease, reason=_REAPED, via="reap")
        # A live, beating worker runs on.
    if deferred:
        _log.info("reap deferred — locally paused", runner_id=ctx.config.runner_id, count=deferred)
    _CP_REAP_AFTER.reached()


# --------------------------------------------------------------------------- #
# RESUME — the restart re-attach: graceful marking (#12) + crash detection (#13), D-082
# --------------------------------------------------------------------------- #


def mark_resume_intents(store: IWriteRunnerStore, *, now: datetime) -> int:
    """Mark every in-flight lease for same-lease restart-resume — the graceful-shutdown hook (D-082).

    Called once as the daemon exits gracefully (SIGTERM: ``systemctl restart``/stop), *before*
    the workers die, so the next startup's :func:`resume` re-attaches each in-flight session in
    place instead of retrying it fresh. An ungraceful ``kill -9`` never runs this — that case is
    recovered symmetrically by :func:`mark_crash_resume_intents`, which ``host`` runs at startup
    to mark the same intent for a lease killed mid-work (#13), so both restart paths converge on
    the one RESUME step (design/runner/loop.md).

    Marks an **active, non-parked, session-bearing** lease: a parked lease is dormant on a
    question (its own resume is the answer, [ask-answer.md]); a lease with a pending completion/
    decision has its verdict already elicited (its node-step is done, awaiting flush, D-069); a
    lease with no pid/session never reached spawn-return (REAP's residue — nothing to resume).
    Returns the number marked. Store-only — no hub, no process probe — so shutdown stays cheap
    and reachable even when the hub is down.

    Each ``record_resume_intent`` is one durable row, so marking is atomic per lease: a crash
    mid-marking (a ``kill -9`` racing the graceful shutdown) leaves each lease either fully
    marked or not at all. An unmarked in-flight lease simply falls back to the ungraceful path —
    startup crash-recovery (:func:`mark_crash_resume_intents`) re-detects and resumes it — so this
    hook degrades to the crash-recovery contract rather than to a corrupt half-state; there is no
    intra-lease window to guard.
    """
    parked = store.parked_lease_ids()
    pending = store.pending_submission_lease_ids()
    marked = 0
    for lease in store.list_active_leases():
        if lease.pid is None or lease.session_id is None:
            continue
        if lease.lease_id in parked or lease.lease_id in pending:
            continue
        store.record_resume_intent(lease_id=lease.lease_id, marked_at=now)
        marked += 1
    if marked:
        _log.info("marked in-flight leases for restart-resume", count=marked)
    return marked


def mark_crash_resume_intents(store: IWriteRunnerStore, *, process: IProcessProbe, now: datetime) -> int:
    """Detect crash-orphaned sessions at startup and mark them for same-lease resume (#13, D-082).

    The **ungraceful** sibling of :func:`mark_resume_intents`. A ``kill -9`` / OOM / reboot
    never runs the graceful shutdown marker, so the next startup has to find the interrupted
    sessions itself and route them to the *same* RESUME re-attach — instead of ADVANCE reading
    each dead worker as a done declaration (D-055) and failing it verdict-less into a fresh
    retry (D-009), discarding its accumulated context exactly when recovery should keep it.
    Run once by the ``host`` command before the loop starts, symmetric with the graceful marker
    in its shutdown ``finally``; the first tick's :func:`resume` then consumes the marks, so the
    ungraceful path reuses every fence the graceful one already carries — kill-first, the
    unchanged epoch, and the D-088 abandon-if-reassigned ownership check.

    A session-bearing lease is crash-resumable — and marked here — iff **all** hold:

    * its worker's process is **gone** — ``(pid, start_time)`` is no longer live. An
      orphaned-but-alive worker (a bare ``kill -9`` of only the runner pid left its children
      running) is skipped: it is re-adopted through its own live heartbeat on the ``Restart=
      always`` bounce, never re-spawned;
    * it recorded **no session-end** — the ``SessionEnd`` hook never fired, so the worker did
      not declare done. A dead pid *with* a session-end is a clean exit ADVANCE judges (the
      acceptance split this issue turns on);
    * its heartbeat is **not stale** *as measured at crash time* — it was actively working when
      killed. A worker already stalled at crash time is left to today's reap/verdict-less-fail
      path and retried per the node's ``retries`` (unchanged) — resuming a wedged session would
      only wedge it again.

    Staleness is measured against :meth:`last_daemon_liveness` — when the daemon was last known
    alive — not against the clock at recovery. The question is whether the worker had stopped
    working *before the daemon died*, and ``now - last_heartbeat`` cannot answer it: at startup
    that is ``downtime + idle-at-crash``, so any outage past the threshold would read every
    in-flight lease as stalled and skip it — silently degrading exactly the reboot/OOM cases
    this issue exists for into the fresh-retry path it exists to prevent. ``now`` remains the
    fallback for a store that never ticked, which by construction holds no in-flight lease.

    Parked (dormant on a question, resumed by its answer) and pending-submission (outcome
    already elicited, awaiting flush) leases are skipped for the same reasons the graceful
    marker skips them. Marking is one-shot by construction: this runs only at startup, never
    per tick, so a resume that itself fails (missing/corrupt session, stale-epoch first write)
    is not re-marked — its resumed process exits and ADVANCE requeues it fresh, the self-heal
    the graceful path already relies on. Returns the number marked.
    """
    parked = store.parked_lease_ids()
    pending = store.pending_submission_lease_ids()
    ended = store.session_ended_lease_ids()
    # as_utc: this instant is about to be subtracted from, and a naive one would
    # silently compare wrong. UtcDateTime reads it back aware, so this is a guard.
    last_alive = store.last_daemon_liveness()
    crashed_at = as_utc(last_alive) if last_alive is not None else now
    marked = 0
    for lease in store.list_active_leases():
        if lease.pid is None or lease.session_id is None:
            continue  # never reached spawn-return — REAP's residue, nothing to resume
        if lease.lease_id in parked or lease.lease_id in pending:
            continue  # dormant on a question / outcome already elicited — not a crash to resume
        if lease.lease_id in ended:
            continue  # declared done (SessionEnd fired) — ADVANCE judges it (exit-is-done, D-055)
        if process.is_alive(lease.pid, lease.process_start_time or ""):
            continue  # orphaned-but-alive — re-adopted via its live heartbeat, never re-spawned
        if is_heartbeat_stale(store, lease, crashed_at):
            continue  # stalled at crash time — reaped & retried per the node's budget, unchanged
        store.record_resume_intent(lease_id=lease.lease_id, marked_at=now)
        marked += 1
    if marked:
        _log.info("marked crash-interrupted leases for restart-resume", count=marked)
    return marked


def resume(ctx: LoopContext) -> None:
    """Re-attach to in-flight sessions a restart marked for same-lease resume (D-082) — startup recovery.

    A no-op on every normal tick (nothing is marked); non-empty only on the first tick after a
    restart, whether marked by the graceful shutdown hook (#12) or by ``host``'s startup crash-
    recovery scan when a ``kill -9`` / reboot skipped that hook (#13). Both write the same
    resume-intent, so this step is indifferent to which; each marked lease is either **resumed in
    place** — under the unchanged
    ``lease_id``/``epoch``/``session_id``, only ``pid``/``process_start_time`` rewritten, no retry
    consumed (D-078) — or, if the hub reassigned/detached the chunk while the runner was down
    (D-088), **abandoned**: released with no epoch bump, so the runner never re-asserts authority
    over work that is now another runner's.

    Runs before ADVANCE so a resumed lease reads live again by the time ADVANCE iterates — its
    fresh pid keeps ADVANCE from mistaking the killed-mid-work worker for a done declaration and
    eliciting a verdict-less failure. A lease marked but no longer active (closed while the runner
    was down) just has its intent cleared so it does not linger."""
    intents = ctx.store.resume_intent_lease_ids()
    if not intents:
        return
    _CP_RESUME_BEFORE.reached()  # marked intents present; a crash here re-runs RESUME unchanged
    active = {lease.lease_id: lease for lease in ctx.store.list_active_leases()}
    for lease_id in intents:
        lease = active.get(lease_id)
        if lease is None:
            ctx.store.record_resume_clear(lease_id=lease_id, cleared_at=ctx.clock.now())
            continue
        _resume_marked_lease(ctx, lease)


def _resume_marked_lease(ctx: LoopContext, lease: LeaseRecord) -> None:
    """Resume a marked lease in place, or abandon it if the hub reassigned its chunk (D-082/D-088),
    or if the hub no longer knows it at all (blizzard#9)."""
    try:
        detail = ctx.hub.get_chunk(lease.chunk_id)
    except ChunkNotFoundError:
        # The chunk is gone outright (e.g. a store reset) — terminal, not retryable; abandon now
        # rather than leave the intent open for PULL's `_release_detached` to find it later.
        _abandon_reassigned(ctx, lease, via="resume")
        return
    except HubClientError:
        # Hub unreachable — the intent is durable and the environments stay held (D-083), so
        # leave it open and retry next tick. Resuming blind would risk re-asserting authority
        # over a chunk that may have been reassigned; the ownership check is worth the wait.
        return
    ours = detail.route is not None and detail.route.runner_id == ctx.config.runner_id
    if detail.status == ChunkStatus.RUNNING and ours:
        _resume_in_place(ctx, lease)
    else:
        _abandon_reassigned(ctx, lease, via="resume")


def _resume_in_place(ctx: LoopContext, lease: LeaseRecord) -> None:
    """Kill any survivor, then resume the session under the same lease/epoch/session (D-082/D-049).

    The fourth sibling of the resume family (spawn / judgement / answer): kill-first is what
    prevents two processes on one session — the epoch is not (D-049) — and the session id, lease
    id, and epoch are all preserved, so the resumed worker's eventual completion carries the
    original epoch and the hub accepts it in place. Only ``pid``/``process_start_time`` are
    rewritten; no lease is minted and no closure is recorded, so no retry is consumed (D-078).

    A missing/corrupt session self-heals via the existing failure path: the resumed process
    cannot find its session, exits, and ADVANCE's verdict-less-exit failure requeues it fresh
    (D-009) — no explicit detection needed here.

    Crash windows (``bzh:crash-point-registry``). Kill-first closes the *original* worker's
    survivor window: a crash after ``_CP_RESUME_AFTER_KILL`` re-runs RESUME, whose kill of the
    (now-dead) recorded pid is a no-op before it re-attaches — one process. The one window
    kill-first cannot guard is the sub-millisecond gap between ``resume_with_message`` returning
    a pid and ``record_spawn`` making it durable: a crash there leaves a live re-attached worker
    whose pid was never recorded, so the re-run kills the stale recorded pid (not the survivor)
    and re-attaches a *second* process to the same session. This is the **same by-construction
    spawn-record gap** the fresh spawn (``_spawn_attempt``) and the answer-resume
    (``_resume_if_answered``) already carry — no crash point can arm a window whose recovery
    input (the new pid) does not yet exist — so it is left un-armed here too rather than asserted
    away. It is bounded to that one call-return→store-write gap (design/runner/loop.md).

    Gated by the local brake (issue #45) **before the kill** — gating after would kill the
    survivor and leave it not re-attached, the one behavior explicitly out of scope. A
    suppressed resume leaves the marked intent open; RESUME re-asks it every tick until the
    brake clears. Left untouched, the lease is exactly the shape ADVANCE's exited-worker
    judge would otherwise select — active, session-bearing, dead pid, not pending, not
    parked — so :func:`advance` skips any lease whose resume intent is still open, the same
    way it skips a pending or parked one; RESUME, not ADVANCE, owns it until the intent
    clears."""
    if _spawn_suppressed(ctx, via="resume", chunk_id=lease.chunk_id, lease_id=lease.lease_id):
        return
    now = ctx.clock.now()
    if lease.pid is not None:
        ctx.process.kill(lease.pid)  # kill-first — never two processes on one session (D-049)
    _CP_RESUME_AFTER_KILL.reached()  # survivor killed; re-run kills the dead pid (no-op) then re-attaches
    bindings = ctx.store.bindings_for_chunk(lease.chunk_id)
    if not bindings or lease.session_id is None:
        _log.warning(
            "marked lease has no warm env/session — abandoning", chunk_id=lease.chunk_id, lease_id=lease.lease_id
        )
        _abandon_reassigned(ctx, lease, killed=True, via="resume")
        return
    # The resume-with-message → record_spawn gap is the un-armable spawn-record window (see the
    # docstring): the same one SPAWN and answer-resume carry, not a new one this step introduces.
    pid = ctx.harness.resume_with_message(bindings[0].workdir, lease.session_id, _RESTART_RESUME_MESSAGE)
    ctx.store.record_spawn(
        lease.lease_id,
        pid=pid,
        process_start_time=ctx.process.start_time(pid) or "",
        session_id=lease.session_id,  # unchanged — same session under the same lease (D-082)
        spawned_at=now,
    )
    ctx.store.record_resume_clear(lease_id=lease.lease_id, cleared_at=now)
    _CP_RESUME_AFTER.reached()  # pid recorded, intent cleared — a crash here re-runs RESUME as a no-op
    _log.info(
        "resumed in-flight session after restart",
        chunk_id=lease.chunk_id,
        lease_id=lease.lease_id,
        epoch=lease.epoch,
        pid=pid,
    )


def _abandon_reassigned(ctx: LoopContext, lease: LeaseRecord, *, killed: bool = False, via: str) -> None:
    """Release a chunk the hub reassigned, detached, or no longer knows about (D-088/blizzard#9) —
    reached from restart-resume or a live tick.

    No epoch bump and no requeue: the chunk is another runner's now (or detached to ``ready``, or
    gone outright — a 404 at the hub, e.g. after a store reset), so re-asserting authority over it
    would be wrong — the runner learns of it, whether over its own restart or on a live tick, and
    does exactly what D-088 asks: kill the worker, release the environments. The lease is closed
    ``released`` (not a failed attempt — it never gets to run) and the intent is cleared. ``via``
    names which caller reached the ownership check that led here (``"resume"`` — restart-resume,
    ``"pull"`` — a live tick's :func:`_release_detached`, ``"reap"`` — an escalation REAP
    suppressed in favor of this abandon, see :func:`_fail_attempt`) so the log line below does not
    overclaim a single cause."""
    now = ctx.clock.now()
    if lease.pid is not None and not killed:
        ctx.process.kill(lease.pid)
    _CP_ABANDON_AFTER_KILL.reached()  # worker killed; envs not yet released — recovery is the next tick's re-scan
    _release_all(ctx, lease.chunk_id)
    ctx.store.record_closure(
        lease_id=lease.lease_id, chunk_id=lease.chunk_id, node_id=lease.node_id, reason=_RELEASED, closed_at=now
    )
    ctx.store.record_resume_clear(lease_id=lease.lease_id, cleared_at=now)
    _log.info("abandoned reassigned/detached/unknown chunk", chunk_id=lease.chunk_id, lease_id=lease.lease_id, via=via)


# --------------------------------------------------------------------------- #
# PULL
# --------------------------------------------------------------------------- #


def pull(ctx: LoopContext) -> None:
    """Exchange facts with the hub (outbound-only, D-012): sync the registry, learn of any
    detach/reassignment, drain the buffer.

    Three outbound exchanges happen here. First :func:`_sync_registry` registers the runner
    (idempotent — refreshing its ``last_seen_at`` liveness, D-070) and reads its declarative
    pause brake back, mirroring it locally so FILL adheres (D-043). Then :func:`_release_detached`
    asks the hub, per active lease, whether this runner still holds the route — the same
    ownership question restart-resume already asks — and abandons any lease it no longer holds
    (D-088), before anything is flushed. Then the outbound buffer drains.

    Store-and-forward always (D-069): every hub-bound fact was written to the buffer at mint
    with a per-runner monotonic seq, and this is the single flusher that drains it — FIFO, so
    a ``lease.minted`` always precedes the completion minted under it. A completion's flush is
    special: its apply-response carries the chunk's next node envelope (D-072), so the flusher
    drives the continue-in-place here. A transport failure stops the drain (the buffer is the
    only ordered path — a later fact must not overtake a stuck earlier one) and the backlog
    flushes next tick; an outage is just a bigger backlog.
    """
    _sync_registry(ctx)
    _release_detached(ctx)
    _CP_PULL_BEFORE.reached()
    flush_outbound(ctx)
    _CP_PULL_AFTER.reached()


def _sync_registry(ctx: LoopContext) -> None:
    """Register + heartbeat (D-070) and mirror the hub's pause brake locally (D-043/D-012).

    Registration is idempotent and doubles as the runner-level liveness heartbeat — a
    per-pull refresh of ``last_seen_at``, much slower than the machine-local worker
    heartbeat (D-070). The declarative pause brake is then read back and mirrored to the
    runner store so FILL adheres without a hub call; when the hub is unreachable the last
    mirrored value holds, so the runner keeps obeying its last-known directive (D-012).
    """
    try:
        ctx.hub.register_runner(ctx.config.runner_id, ctx.config.workspace_id)
        paused = ctx.hub.fetch_runner_paused(ctx.config.runner_id)
    except HubClientError:
        return  # hub unreachable — keep the last-mirrored brake (D-012)
    ctx.store.set_hub_paused(ctx.config.runner_id, paused=paused, at=ctx.clock.now())


def _release_detached(ctx: LoopContext) -> None:
    """Abandon any active lease the hub no longer routes to this runner (D-088) — a live tick's
    half of restart-resume's ownership check (:func:`_resume_marked_lease`).

    For every active lease, ask the hub who holds the chunk's route now. Unreachable hub →
    ``continue``: keep working, the last-known directive holds (D-012, the same rule
    :func:`_sync_registry` follows) — do not crash, do not abandon on a transport failure. Ours
    and still routed here → leave it alone, whatever its derived status: a live runner legitimately
    holds an active lease while the chunk derives ``delivering``, ``waiting_on_human``, or
    ``needs_human`` (a hub-node hold or an open escalation), so — unlike the restart-resume
    predicate, which also checks ``status == RUNNING`` because at restart a non-running status
    means the world moved on — the check here is route identity **alone**: ``route is None``
    (detached) or ``route.runner_id`` is someone else's (reassigned). Either one is
    :func:`_abandon_reassigned`: kill the worker, release every environment, close the lease
    ``released`` with no epoch bump, no requeue fact, no retry consumed.

    Runs before the flush, deliberately: killing the detached chunk's worker as early **within
    this step** as possible is the best lever the runner has on the late-write window — between
    the detach and the chunk's re-claim by some runner, this runner's already-buffered facts for
    the chunk can still flush and be accepted; only a new lease's floor closes that (D-035/D-044).
    It is not the earliest point in the *tick* — REAP and RESUME both precede PULL, and REAP's own
    failed-attempt path (:func:`_fail_attempt`) makes the same ownership check before escalating,
    so a detach discovered there is abandoned on the spot rather than left for this pass to find.
    Killing the worker before the flush narrows the window but cannot purge the buffer:
    ``bzh:invariant-checker`` requires a gapless outbound-buffer sequence, so deleting buffered
    facts to close it would trade a durable invariant for a window the fence closes anyway. This is
    requeue's existing window (D-067 releases the route with no bump too) — not engineered around
    here."""
    for lease in ctx.store.list_active_leases():
        if _reassigned_or_detached(ctx, lease):
            _abandon_reassigned(ctx, lease, via="pull")


def _reassigned_or_detached(ctx: LoopContext, lease: LeaseRecord) -> bool:
    """True iff the hub no longer routes ``lease``'s chunk to this runner (D-088), or the
    chunk is gone outright (blizzard#9).

    Unreachable hub → ``False``: last-known directive holds (D-012) — a transport failure is
    never read as a detach. A 404 (:class:`ChunkNotFoundError`) is the one exception to that
    rule: the hub telling us the chunk no longer exists (e.g. a store reset) is not a transport
    failure to wait out — it is terminal, so this reads it as detached too and lets the caller's
    abandon path reap the lease and release the held environments rather than retry the 404
    forever. Shared by :func:`_release_detached` (a live-tick sweep over every active lease) and
    :func:`_fail_attempt`'s escalate guard (a single lease, checked only on the exhausted-retries
    path) — the same ownership question, asked at two different rates."""
    try:
        detail = ctx.hub.get_chunk(lease.chunk_id)
    except ChunkNotFoundError:
        return True  # the chunk no longer exists at the hub — terminal, not retryable
    except HubClientError:
        return False  # hub unreachable — last-known directive holds (D-012); keep working
    return detail.route is None or detail.route.runner_id != ctx.config.runner_id


def flush_outbound(ctx: LoopContext) -> None:
    """Drain the outbound buffer in FIFO order until a fact fails to deliver (D-069)."""
    for fact in ctx.store.pending_outbound():
        if not _flush_one(ctx, fact):
            break  # transport failure — stop; strict FIFO, retry the backlog next tick


def _flush_one(ctx: LoopContext, fact: BufferedFact) -> bool:
    """Deliver one buffered fact. Return False on a transport failure (stop the drain)."""
    if fact.kind == _COMPLETION_KIND:
        return _flush_completion(ctx, fact)
    if fact.kind == _DECISION_KIND:
        return _flush_decision(ctx, fact)
    return _flush_hub_fact(ctx, fact)


def _flush_hub_fact(ctx: LoopContext, fact: BufferedFact) -> bool:
    """Push a ``lease.minted`` / ``escalation.recorded`` fact to POST /events (D-069)."""
    payload = json.loads(fact.payload)
    batch = RunnerFactBatch(
        runner_id=ctx.config.runner_id,
        facts=[RunnerFact(seq=fact.seq, kind=fact.kind, payload=payload)],
    )
    try:
        ack = ctx.hub.push_facts(batch)
    except HubClientError:
        return False  # hub unreachable — the fact stays buffered, retried next tick
    if fact.seq in ack.rejected:
        # A contract rejection (unknown kind) is not idempotency — surface it, but do
        # not wedge the FIFO drain on a fact the hub will never accept: ack and move on.
        _log.error("hub rejected buffered fact", seq=fact.seq, kind=fact.kind)
    ctx.store.ack_outbound(fact.seq, acked_at=ctx.clock.now())
    return True


def _flush_completion(ctx: LoopContext, fact: BufferedFact) -> bool:
    """Submit a buffered completion and drive its apply-response (D-036/D-072/D-090).

    Idempotent by construction: the hub's completion apply is epoch-idempotent (a
    re-applied completion returns its original outcome without a second transition,
    D-090), and the runner acts on the response only while the lease is still active —
    a re-flush after a lost ack finds the lease closed and simply clears the buffer.
    """
    payload = json.loads(fact.payload)
    submission = CompletionSubmission.model_validate(payload["submission"])
    _CP_FLUSH_BEFORE_SUBMIT.reached()
    try:
        response = ctx.hub.submit_completion(fact.chunk_id or "", submission)
    except HubClientError:
        return False  # completion stays durable in the buffer; the mid-node worker is unaffected

    _CP_FLUSH_AFTER_SUBMIT.reached()  # hub applied it; a crash here is the lost-ack replay (D-090)
    ctx.store.ack_outbound(fact.seq, acked_at=ctx.clock.now())
    _CP_FLUSH_AFTER_ACK.reached()
    lease = ctx.store.active_lease(fact.lease_id or "")
    if lease is None:
        # Already advanced on an earlier flush whose ack was lost (D-090) — nothing to do.
        return True
    _consume_apply_response(ctx, lease, response)
    _CP_FLUSH_AFTER_APPLY.reached()
    return True


def _flush_decision(ctx: LoopContext, fact: BufferedFact) -> bool:
    """Submit a buffered runner-config gate decision and park the chunk (D-032/D-045).

    A gated node's decision parks the chunk ``waiting_on_human`` — there is no next
    envelope to continue into, so the flush just closes the lease (the node-step is
    done) and holds the environments. Idempotent by construction: the hub's decision
    apply is natural-key idempotent (a re-submitted decision at the same (node, epoch)
    returns the parked outcome without a second row, D-045), and a re-flush past a lost
    ack finds the lease closed and clears the buffer.
    """
    payload = json.loads(fact.payload)
    submission = DecisionSubmission.model_validate(payload["submission"])
    try:
        response = ctx.hub.submit_decision(fact.chunk_id or "", submission)
    except HubClientError:
        return False  # decision stays durable in the buffer; retried next tick

    ctx.store.ack_outbound(fact.seq, acked_at=ctx.clock.now())
    lease = ctx.store.active_lease(fact.lease_id or "")
    if lease is None:
        return True  # already parked on an earlier flush whose ack was lost (D-045)
    if response.outcome == ApplyOutcome.FAILURE:
        _log.warning("decision rejected on flush", chunk_id=lease.chunk_id, detail=response.detail or "")
        _fail_attempt(ctx, lease, reason=_FAILED, via="pull")
        return True
    ctx.store.record_closure(
        lease_id=lease.lease_id,
        chunk_id=lease.chunk_id,
        node_id=lease.node_id,
        reason=_PARKED,
        closed_at=ctx.clock.now(),
    )
    _log.info("chunk parked at runner-config gate", chunk_id=lease.chunk_id, node=lease.node_name)
    return True


def _consume_apply_response(ctx: LoopContext, lease: LeaseRecord, response: ApplyResponse) -> None:
    """Record the closure and continue in place per the hub's apply-response (D-072)."""
    if response.outcome == ApplyOutcome.FAILURE:
        # A semantic rejection — a stale-epoch (zombie) or terminal completion. The
        # attempt failed; requeue or escalate. The chunk never advanced and never
        # entered the merge queue (the hub fenced it before any write, D-007).
        _log.warning("completion rejected on flush", chunk_id=lease.chunk_id, detail=response.detail or "")
        _fail_attempt(ctx, lease, reason=_FAILED, via="pull")
        return
    now = ctx.clock.now()
    ctx.store.record_closure(
        lease_id=lease.lease_id, chunk_id=lease.chunk_id, node_id=lease.node_id, reason=_TRANSITIONED, closed_at=now
    )
    bindings = ctx.store.bindings_for_chunk(lease.chunk_id)
    _apply_response(ctx, lease.chunk_id, response.outcome, response.next_envelope, bindings)


# --------------------------------------------------------------------------- #
# FILL
# --------------------------------------------------------------------------- #


def fill(ctx: LoopContext) -> None:
    """Keep the fleet busy: peek → acquire → claim-by-route → bind → spawn (D-080).

    Since D-080, FILL is where work is claimed. Open agent slots are
    ``MAX_AGENTS - active_leases``; for each, peek the ready queue, acquire the
    chunk's environments (all-or-nothing), and POST the complete route. A 409 is
    race-second-place — release the bindings and move on. The winning claim carries
    the first node envelope, so the worker starts without a second round-trip.

    The pause brake (D-043) has two independent surfaces and FILL claims nothing while
    **either** is set: the hub's flag (mirrored locally by PULL) and this runner's own
    local flag (``PATCH /runner``, issue #43), which the operator sets machine-locally and
    which therefore holds with the hub unreachable. In-flight chunks are untouched under
    either — FILL only ever stops *new* claims — but since issue #45 the two brakes'
    reach beyond FILL diverges: the hub brake keeps its D-043 claims-only meaning (checked
    here alone), while the local brake also blocks every other spawn site (restart-resume,
    an answer-resume, ADVANCE's next-node, a requeue or claim-adopt respawn, and ADVANCE's
    judgement resume) via :func:`_spawn_suppressed`, its one shared home, and defers
    escalation (:func:`_fail_attempt`'s exhausted-budget branch) the same way REAP's own
    kill of a stalled worker is deferred — a locally-paused runner starts no process and
    hands nothing off as unrecoverable while it waits. So a hub-only pause still drains the
    fleet the way it always has ([loop.md]); a local pause spawns nothing, anywhere, while
    leaving every lease, route, and retry budget exactly as it was.

    Recovery runs first: :func:`_reconcile_interrupted_claims` reconciles any binding
    left by a crash in FILL's own bind→claim→spawn window **before** new work is peeked,
    so a released orphan frees its environment for this same tick and an adopted claim is
    never double-claimed off the ready queue. It runs even while paused — it recovers
    in-flight work, it does not start new work.
    """
    _reconcile_interrupted_claims(ctx)
    hub_paused = ctx.store.hub_paused(ctx.config.runner_id)
    local_paused = ctx.store.local_paused(ctx.config.runner_id)
    if hub_paused or local_paused:
        _log.info(
            "paused — no new claims this tick",
            runner_id=ctx.config.runner_id,
            hub_paused=hub_paused,
            local_paused=local_paused,
        )
        return
    slots = ctx.config.max_agents - len(ctx.store.list_active_leases())
    for _ in range(max(slots, 0)):
        if not _fill_one(ctx):
            break


def _reconcile_interrupted_claims(ctx: LoopContext) -> None:
    """Reconcile bindings left by a crash in FILL's bind→claim→spawn window (D-083).

    Because the binding is written locally *before* the hub claim, a crash anywhere in
    that window leaves the runner holding a binding for a chunk with no active lease.
    This runs before FILL peeks new work and, per the hub's view of each such chunk —

      * route ours, still ``running`` → **adopt**: spawn the current node into the warm
        environment, finishing the interrupted claim (the lease never minted);
      * no live route (``ready``), or a route held by another runner → **release** the
        orphaned binding (the claim never landed, or we lost the race before retracting
        it) so the environment frees this tick and the chunk re-derives ``ready``.

    A chunk at a hub node (``delivering``) or awaiting a human keeps its binding and is
    left to ADVANCE — only a chunk the runner should be actively working, but isn't, is
    reconciled here.

    A 404 (:class:`ChunkNotFoundError`) is a third, terminal shape, same as
    :func:`_advance_held_chunk` (blizzard#9): the hub no longer knows this chunk, so the
    orphaned binding is released rather than left for this reconciler to keep re-asking
    about forever — the generic :class:`HubClientError` branch below is for a transport
    failure alone, not this one."""
    for chunk_id in ctx.store.live_tenure_chunk_ids():
        if ctx.store.active_lease_for_chunk(chunk_id) is not None:
            continue  # a live worker holds it — REAP/ADVANCE own it
        try:
            detail = ctx.hub.get_chunk(chunk_id)
        except ChunkNotFoundError:
            _log.warning("hub reports interrupted-claim chunk unknown — releasing envs", chunk_id=chunk_id)
            _release_all(ctx, chunk_id)
            continue
        except HubClientError:
            continue  # hub unreachable — the binding is durable; retry next tick
        if detail.decision is not None:
            # A chunk carrying a live gate decision — open (``waiting_on_human``) or
            # resolved-but-not-transitioned — is owned by ADVANCE's :func:`_advance_held_chunk`,
            # which records the resolving transition (D-045). A *resolved* gate keeps its route
            # live so it derives ``running`` with no active lease (D-027) — the same shape as an
            # interrupted claim — so without this guard the adopt branch below would spawn a
            # worker on the human-judged node, bumping the epoch out from under the human's
            # resolving transition. This is the "awaiting a human … left to ADVANCE" case.
            continue
        bindings = ctx.store.bindings_for_chunk(chunk_id)
        if not bindings:
            continue
        ours = detail.route is not None and detail.route.runner_id == ctx.config.runner_id
        if detail.status == ChunkStatus.RUNNING and ours:
            _adopt_interrupted_claim(ctx, chunk_id)  # route ours — just spawn the current node
        elif detail.status == ChunkStatus.READY:
            _reclaim_interrupted(ctx, chunk_id, bindings)  # claim never landed — claim now, reuse the binding
        elif detail.route is not None and not ours:
            _log.info("releasing binding — another runner won the chunk", chunk_id=chunk_id)
            _release_all(ctx, chunk_id)


def _fill_one(ctx: LoopContext) -> bool:
    """Claim and start one chunk. Returns False when nothing more can be filled."""
    try:
        peek = ctx.hub.peek_queue()
    except HubClientError:
        return False  # hub unreachable — try next tick
    if not peek.entries:
        return False

    entry = peek.entries[0]
    held = ctx.store.held_environment_ids()
    _CP_FILL_BEFORE_ACQUIRE.reached()
    try:
        acquired = ctx.provider.acquire(entry.chunk_id, _SOLO_ENV_COUNT, held)
    except EnvironmentPreparationError as exc:
        # Not capacity — a reset-on-acquire step failed (D-021). Surface it as an
        # attributable FILL error; the provider aborted rather than hand over a
        # half-reset env, so the chunk simply waits for a fixed workspace.
        _log.error(
            "environment preparation failed at FILL",
            chunk_id=entry.chunk_id,
            environment_id=exc.environment_id,
            step=exc.step,
            detail=str(exc),
        )
        return False
    except WorkspaceAcquisitionError:
        _log.info("acquire refused — env-bound this tick", chunk_id=entry.chunk_id)
        return False  # env capacity exhausted; the chunk waits

    # Record the chunk→env binding locally BEFORE claiming at the hub (D-083): the binding
    # is the runner's durable anchor for a chunk it holds, so a crash in the bind→claim→spawn
    # window leaves a local trace :func:`_reconcile_interrupted_claims` recovers next tick —
    # without it, a crash after a won claim but before any local write would strand the chunk
    # (the hub shows it claimed, the runner has nothing to drive or reap).
    _CP_FILL_AFTER_ACQUIRE.reached()
    now = ctx.clock.now()
    for a in acquired:
        ctx.store.record_binding(
            chunk_id=entry.chunk_id, environment_id=a.environment_id, workdir=a.workdir, bound_at=now
        )
    _CP_FILL_AFTER_BIND.reached()

    claim = RouteClaim(
        chunk_id=entry.chunk_id,
        runner_id=ctx.config.runner_id,
        workspace_id=ctx.config.workspace_id,
        environment_ids=[a.environment_id for a in acquired],
    )
    try:
        outcome = ctx.hub.claim_route(claim)
    except HubClientError:
        _release_binding(ctx, entry.chunk_id, acquired)  # claim not sent — undo the local binding
        return False
    if outcome.conflict is not None or outcome.claimed is None:
        _log.info("route claim lost the race", chunk_id=entry.chunk_id)
        _release_binding(ctx, entry.chunk_id, acquired)  # someone else won — undo our binding
        return True  # peek fresh next iteration

    _CP_FILL_AFTER_CLAIM.reached()
    _spawn_attempt(ctx, entry.chunk_id, outcome.claimed.envelope, acquired, via="fill")
    return True


# --------------------------------------------------------------------------- #
# ADVANCE
# --------------------------------------------------------------------------- #


def advance(ctx: LoopContext) -> None:
    """Judge finished workers and move chunks through the graph (D-025/D-027).

    Two responsibilities: (a) a session-bearing worker whose process has exited is a
    done declaration — resume it with the judgement prompt, parse the ``<Choice>``,
    push its artifacts, and **buffer** the epoch-fenced completion (the flusher in
    PULL delivers it and drives the apply-response, D-069) — unless this operator gates
    the node by name, in which case it buffers a **decision** instead (D-032); (b) a
    chunk the runner holds with no active lease is driven by :func:`_advance_held_chunk`
    — a hub node polled for its terminal outcome (D-066), or a gate whose decision the
    human has resolved advanced by the resolving transition (D-045).

    A worker whose completion or decision is already buffered is skipped: the outcome is
    elicited exactly once, then the chunk waits at its node boundary for the flush (D-069).

    A lease with an **open resume intent** is skipped too (issue #45): RESUME, not this
    step, owns it until the intent clears. This is not just a pause artifact — it holds
    on every tick, restart or not. On an ordinary restart RESUME already resolved every
    marked lease (re-attached it or abandoned it) earlier in the same tick, so this set
    is empty by the time ADVANCE runs and the skip is inert; it only ever bites when
    RESUME left the intent open — the runner's own brake is on (:func:`_resume_in_place`
    suppressed), or the hub was unreachable for the ownership check. Either way, the
    lease left behind is *exactly* the shape this loop would otherwise read as exited
    work — active, session-bearing, dead pid — and judging it here would be wrong
    twice over: it elicits a verdict from a worker RESUME never got to re-attach
    (:meth:`ctx.harness.judge` resumes the session headlessly, a real spawn the local
    brake forbids), and a worker killed mid-work is not a done declaration (D-055) even
    though its process is gone.
    """
    pending = ctx.store.pending_submission_lease_ids()
    parked = ctx.store.parked_lease_ids()
    resume_intents = ctx.store.resume_intent_lease_ids()
    for lease in ctx.store.list_active_leases():
        if lease.pid is None or lease.session_id is None:
            continue  # REAP's residue
        if lease.lease_id in resume_intents:
            continue  # RESUME hasn't re-attached (or abandoned) it yet — not exited work
        if lease.lease_id in pending:
            continue  # outcome elicited, awaiting flush — the node boundary (D-069)
        if lease.lease_id in parked:
            _resume_if_answered(ctx, lease)  # dormant on a question — resume on the answer
            continue
        if ctx.process.is_alive(lease.pid, lease.process_start_time or ""):
            continue  # worker still running
        _advance_exited_worker(ctx, lease)

    for chunk_id in ctx.store.live_tenure_chunk_ids():
        if ctx.store.active_lease_for_chunk(chunk_id) is None:
            _advance_held_chunk(ctx, chunk_id)


def _advance_exited_worker(ctx: LoopContext, lease: LeaseRecord) -> None:
    """Park on an open ask, else elicit the verdict and buffer the completion (D-069/D-009).

    The judgement elicitation (below) is a real spawn — :meth:`ctx.harness.judge` resumes
    the exited worker's session headlessly to capture its verdict reply — so it is gated
    by the local brake (issue #45) the same as the other three primitives, just placed
    later in this function: the ask-park and gate-decision branches above it end the
    attempt with no process started (a park or a human decision, not a judgement), and
    the artifact push is idempotent forge state, not a spawn, so none of those need the
    gate. Only the judge call does. A suppressed judgement leaves the lease exactly as it
    was — active, session-bearing, dead pid, no completion buffered — so ADVANCE retries
    it every tick until the brake clears, the same self-driving shape every other gate in
    this module leaves behind.
    """
    if lease.session_id is None:
        return  # not spawned — REAP's residue (guarded by the caller too)

    # Ask-and-exit ([ask-answer.md]): a worker that exited holding an unforwarded ask
    # parked on a question — forward it and park, no verdict, no retry consumed. This is
    # what D-009 turns on: an exit with an open ask is a park; an exit with neither is a
    # failure. The park fact stops REAP's clock and makes the chunk derive waiting_on_human.
    # Not a spawn, so it proceeds regardless of the local brake.
    ask = ctx.store.unforwarded_ask(lease.lease_id)
    if ask is not None:
        _park_on_ask(ctx, lease, ask)
        return

    bindings = ctx.store.bindings_for_chunk(lease.chunk_id)
    if not bindings:
        _log.warning("exited worker with no bound env — skipping", chunk_id=lease.chunk_id)
        return

    try:
        envelope = ctx.hub.get_envelope(lease.chunk_id)
    except HubClientError:
        return  # hub unreachable — the worker's exit is durable; retry next tick

    # 1. Push produced branches to their forge origins BEFORE submitting (D-026). Not a
    #    harness spawn, and idempotent, so this runs regardless of the local brake — the
    #    branch is forge state the runner already holds, not new work being started.
    _CP_ADV_BEFORE_PUSH.reached()
    artifacts = _push_and_collect_artifacts(ctx, bindings)
    _CP_ADV_AFTER_PUSH.reached()

    # 1b. Runner-config gate (D-032/D-073): this operator gates this node by name, so the
    #     node-step's outcome is a human's, not the worker's. Submit a Decision carrying
    #     the step's artifacts instead of eliciting a verdict — the human judges (D-045).
    #     Not a spawn either — parking for a human is not starting a process — so this
    #     also proceeds regardless of the local brake.
    if lease.node_name in ctx.config.gates:
        _buffer_decision(ctx, lease, artifacts)
        return

    # 2. Elicit the verdict via the judgement resume (D-038) — the fourth spawn primitive
    #    (issue #45), gated here rather than hoisted to the top of this function so the
    #    park/gate/push work above (none of it a spawn) still happens while paused.
    if _spawn_suppressed(ctx, via="advance", chunk_id=lease.chunk_id, lease_id=lease.lease_id):
        return

    # A dead worker whose session cannot answer a parseable <Choice> is a failure (D-009).
    prompt = (envelope.judgement_prompt or "") + _elicitation_tail(envelope)
    # The adapter works in a directory; the runner resolves the provider-returned
    # workdir from the binding and supplies it (design/runner/environments.md).
    output = ctx.harness.judge(bindings[0].workdir, lease.session_id, prompt)
    choice = ctx.harness.parse_verdict(output)
    if choice is None:
        _log.warning("verdict-less judgement — failing attempt", chunk_id=lease.chunk_id, lease_id=lease.lease_id)
        _fail_attempt(ctx, lease, reason=_FAILED, via="advance")
        return
    _CP_ADV_AFTER_JUDGE.reached()

    # 2b. Harvest the node's asset artifacts (D-026): a node that `produces` a name no
    #     pushed git commit covers (the review node's `findings`) emits the worker's
    #     assessment as that asset's content, which a fail judgement carries back into
    #     the build envelope latest-by-epoch (design/workflow-engine.md review node).
    artifacts += _collect_asset_artifacts(envelope, artifacts, ctx.harness.parse_assessment(output))

    # 3. Buffer the completion — one atomic, epoch-fenced write (D-036), delivered by
    #    the flusher (D-069). The buffer entry names the lease so the flush drives its
    #    apply-response; ADVANCE will skip this lease until the flush closes it.
    submission = CompletionSubmission(
        choice=choice,
        epoch=lease.epoch,
        runner_id=ctx.config.runner_id,
        from_node_id=lease.node_id,
        check_results=[],  # in-session check assessment is P7; the model carries them (D-077)
        artifacts=artifacts,
    )
    payload = json.dumps({"submission": submission.model_dump(mode="json")})
    ctx.store.enqueue_outbound(
        kind=_COMPLETION_KIND,
        chunk_id=lease.chunk_id,
        lease_id=lease.lease_id,
        payload=payload,
        created_at=ctx.clock.now(),
    )
    _CP_ADV_AFTER_BUFFER.reached()
    _log.info("completion buffered", chunk_id=lease.chunk_id, lease_id=lease.lease_id, choice=choice)


def _buffer_decision(ctx: LoopContext, lease: LeaseRecord, artifacts: list[SubmittedArtifact]) -> None:
    """Buffer a runner-config gate decision — the gated node-step's outcome (D-032/D-036).

    The node's choice set is the hub's (it owns the graph), so the submission carries
    only the step's artifacts and its fence. The flusher (:func:`_flush_decision`)
    delivers it and parks the chunk; ADVANCE skips this lease until the flush closes it
    (:meth:`pending_submission_lease_ids`).
    """
    submission = DecisionSubmission(
        from_node_id=lease.node_id,
        epoch=lease.epoch,
        runner_id=ctx.config.runner_id,
        artifacts=artifacts,
    )
    payload = json.dumps({"submission": submission.model_dump(mode="json")})
    ctx.store.enqueue_outbound(
        kind=_DECISION_KIND,
        chunk_id=lease.chunk_id,
        lease_id=lease.lease_id,
        payload=payload,
        created_at=ctx.clock.now(),
    )
    _log.info("runner-config gate: decision buffered", chunk_id=lease.chunk_id, node=lease.node_name)


def _apply_response(
    ctx: LoopContext,
    chunk_id: str,
    outcome: ApplyOutcome,
    next_envelope: NodeEnvelope | None,
    bindings: list[EnvBindingRecord],
) -> None:
    """Act on the apply-response: continue in place, hold at a hub node, or finish (D-072)."""
    if outcome == ApplyOutcome.NEXT and next_envelope is not None:
        envs = _bindings_as_environments(bindings)
        _spawn_attempt(ctx, chunk_id, next_envelope, envs, via="apply-response")
    elif outcome == ApplyOutcome.HUB_NODE_TAKEN:
        _log.info("hub node took over — holding envs until terminal", chunk_id=chunk_id)
    elif outcome == ApplyOutcome.DONE:
        _release_all(ctx, chunk_id)
    elif outcome == ApplyOutcome.PARKED_AT_GATE:
        _log.info("chunk parked at human gate", chunk_id=chunk_id)  # waiting_on_human (D-045)


def _advance_held_chunk(ctx: LoopContext, chunk_id: str) -> None:
    """Drive a chunk the runner holds with no active lease: a hub node or a parked gate.

    Two parked shapes share this poll (both hold environments, no live lease): a chunk at
    a **hub node** (deliver) is polled for its terminal outcome and released on landed
    (D-066); a chunk **parked on a resolved gate decision** is advanced by recording the
    resolving transition along the chosen edge (D-027/D-045), then continued in place from
    the returned envelope — the human's choice moves the chunk.

    A 404 (:class:`ChunkNotFoundError`) is a third, terminal shape (blizzard#9): the hub no
    longer knows this chunk (e.g. a store reset), so there is nothing left to poll toward —
    the held environments are released the same way a landed delivery releases them. No lease
    is open here to reap (that is :func:`_reassigned_or_detached`'s job, for the active-lease
    case), just the binding this function already owns.
    """
    try:
        detail = ctx.hub.get_chunk(chunk_id)
    except ChunkNotFoundError:
        _log.warning("hub reports held chunk unknown — releasing envs", chunk_id=chunk_id)
        _release_all(ctx, chunk_id)
        return
    except HubClientError:
        return
    if detail.status == ChunkStatus.DONE:
        _log.info("delivery landed — releasing envs", chunk_id=chunk_id)
        _release_all(ctx, chunk_id)
        return
    decision = detail.decision
    if decision is not None and decision.resolved_choice is not None and not decision.transitioned:
        _resolve_gate(ctx, chunk_id, decision)
    # A conflict routing back to a runner node (D-058) reappears as a fresh envelope
    # the next claim/advance picks up; that recovery cycle is P7. An unresolved decision
    # keeps waiting; the human's resolution is picked up on a later tick.


def _resolve_gate(ctx: LoopContext, chunk_id: str, decision: DecisionView) -> None:
    """Record the resolving transition for a decided gate and continue in place (D-027/D-045).

    The runner authors the transition the human's choice implies — reusing the parked
    step's epoch (no new lease was minted while parked) and referencing the decision id,
    which is what makes a transition out of a human-judged node legal at the hub. The
    apply-response then continues the chunk in its warm environments (spawn the next
    node, hold at a hub node, or finish)."""
    submission = CompletionSubmission(
        choice=decision.resolved_choice or "",
        epoch=decision.epoch,
        runner_id=ctx.config.runner_id,
        from_node_id=decision.node_id,
        artifacts=[],  # the decision's artifacts already landed (D-045)
        decision_id=decision.decision_id,
    )
    try:
        response = ctx.hub.submit_completion(chunk_id, submission)
    except HubClientError:
        return  # the resolution is durable at the hub; retry next tick
    if response.outcome == ApplyOutcome.FAILURE:
        _log.warning("resolving transition rejected", chunk_id=chunk_id, detail=response.detail or "")
        return
    _log.info("gate resolved — advancing chunk", chunk_id=chunk_id, choice=decision.resolved_choice)
    _apply_response(ctx, chunk_id, response.outcome, response.next_envelope, ctx.store.bindings_for_chunk(chunk_id))


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #


def _spawn_suppressed(ctx: LoopContext, *, via: str, chunk_id: str, lease_id: str | None = None) -> bool:
    """True — and logged once — when the runner's own brake blocks this spawn (issue #45).

    Reads **``local_paused`` only**: the hub brake (D-043) keeps its claims-only meaning
    and stays read in FILL alone. The local brake is the machine declining to work, and
    "start no processes on my machine" is a local statement, not a claims-only one — this
    is that gate's one shared home, called before each of the **four** spawn primitives'
    first mutation (:func:`_spawn_attempt`, :func:`_resume_in_place`,
    :func:`_resume_if_answered`, and the judgement resume inside
    :func:`_advance_exited_worker`) so a suppressed spawn writes no fact, kills no pid,
    mints no lease, and elicits no verdict. The lease is left exactly as it was — active,
    unmodified — and the shape it is left in (an interrupted claim, an open resume intent,
    an open park, an unjudged exit) is what the next tick's own recovery re-drives once
    the brake clears; no new state is needed here.

    ``chunk_id`` is always present; ``lease_id`` is ``None`` at :func:`_spawn_attempt` (the
    gate fires before a lease is minted) and carries the held/prior lease at restart-resume,
    answer-resume, and the judgement resume."""
    if not ctx.store.local_paused(ctx.config.runner_id):
        return False
    _log.info(
        "spawn suppressed — locally paused",
        runner_id=ctx.config.runner_id,
        via=via,
        chunk_id=chunk_id,
        lease_id=lease_id,
    )
    return True


def _spawn_attempt(
    ctx: LoopContext, chunk_id: str, envelope: NodeEnvelope, environments: list[AcquiredEnvironment], *, via: str
) -> None:
    """Mint a fresh-epoch lease and spawn a headless worker for a node-step (D-082/D-092).

    Always its caller's final statement, with no post-spawn logic after it (fill/apply-
    response/adopt/reclaim/requeue) — that is what lets the local-pause gate below stay a
    silent ``None`` return indistinguishable from a real spawn (issue #45): there is no
    boolean a caller could misread as "spawn failed" and burn a retry on. A future caller
    that adds post-spawn logic must re-read this contract first. ``via`` names the calling
    site, attributing the gate's suppression log line to it."""
    if _spawn_suppressed(ctx, via=via, chunk_id=chunk_id):
        return
    now = ctx.clock.now()
    epoch = ctx.store.latest_epoch(chunk_id) + 1
    lease_id = mint(LEASE_PREFIX, ctx.clock)
    node = envelope.node
    retries_max = node.retries_max if node.retries_max is not None else ctx.config.default_retries_max
    ctx.store.record_lease(
        NewLease(
            lease_id=lease_id,
            chunk_id=chunk_id,
            graph_id=envelope.graph_id,
            node_id=node.node_id,
            node_name=node.node_name,
            epoch=epoch,
            runner_id=ctx.config.runner_id,
            retries_max=retries_max,
            created_at=now,
        )
    )
    # The lease is a hub-bound fact (D-044): buffer it so the flusher reports it up to
    # POST /events, ahead of any completion minted under it (FIFO, D-069). It is the
    # fence input the hub's completion check consumes — the runner's mint keeps the
    # hub's latest epoch in lockstep across a build -> review chunk, and a requeue's mint
    # closes an escalation by supersession (D-035/D-067).
    ctx.store.enqueue_outbound(
        kind=LEASE_MINTED,
        chunk_id=chunk_id,
        lease_id=lease_id,
        payload=json.dumps({"chunk_id": chunk_id, "epoch": epoch}),
        created_at=now,
    )
    _CP_SPAWN_AFTER_MINT.reached()  # lease minted, worker not spawned — the orphan-lease window REAP absorbs
    # The runner's spawn preamble (issue #17): the effective workspace prompt is the store's
    # runtime override when set, else the static config prompt — read here so an API replace
    # applies to the next spawn with no restart. Rendered with this attempt's machine-local
    # facts, prepended to the envelope prompt; the worker's cwd is the workspace root.
    override = ctx.store.workspace_prompt_override(ctx.config.workspace_id)
    workspace_prompt = override if override is not None else ctx.config.workspace_prompt
    prompt_prefix = render_worker_preamble(
        workspace_prompt=workspace_prompt,
        environments=environments,
        lease_id=lease_id,
        runner_id=ctx.config.runner_id,
        chunk_id=chunk_id,
    )
    preamble = WorkerPreamble(
        environments=environments,
        lease_id=lease_id,
        local_api_url=ctx.config.local_api_url,
        workspace_root=ctx.config.workspace_root,
        prompt_prefix=prompt_prefix,
    )
    handle = ctx.harness.spawn(envelope, preamble, session_hint=str(uuid.uuid4()))
    ctx.store.record_spawn(
        lease_id,
        pid=handle.pid,
        process_start_time=handle.process_start_time,
        session_id=handle.session_id,
        spawned_at=now,
    )
    _CP_SPAWN_AFTER_SPAWN.reached()


def _fail_attempt(ctx: LoopContext, lease: LeaseRecord, *, reason: str, via: str) -> None:
    """Close a failed attempt, then requeue at the node or escalate per the budget (D-078/D-009).

    The exhausted-retries branch checks ownership before escalating (blizzard#38). Tick order is
    REAP -> RESUME -> PULL -> FILL -> ADVANCE, and PULL's own detach sweep
    (:func:`_release_detached`) is what abandons a lease the hub no longer routes here — but a
    caller earlier in the tick (REAP, chiefly) can reach an exhausted retry budget on such a
    lease first. Escalating anyway would buffer an ``escalation.recorded`` fact this same tick's
    PULL cannot retract once flushed — unlike the requeue branch, whose fresh, routeless lease is
    itself caught and abandoned by that later PULL pass, an escalation is a one-way door. So this
    branch re-asks the same ownership question :func:`_release_detached` asks and, if the chunk is
    no longer ours, abandons in place (:func:`_abandon_reassigned`) instead of escalating — the
    same outcome PULL would reach later this tick, without the intervening false escalation.

    **Escalation is deferred while locally paused (issue #45)**, for the same one-way-door
    reason: an ``escalation.recorded`` fact hands the chunk to a human, and a runner that
    has told its operator it will start no processes should not also be handing work off
    as unrecoverable while it waits. The requeue branch above needs no such gate — it
    already self-defers correctly, since its respawn is gated at :func:`_spawn_attempt` and
    :data:`attempt_count` counts mints, which sit below that gate, so no retry is consumed
    by a requeue this function records but that respawn never mints. This one function is
    every caller's escalate path (REAP's orphan case, ADVANCE's verdict-less exit, PULL's
    rejection paths), so gating it here — rather than in each caller — is what keeps them
    all honoring the same brake without three separate checks drifting out of sync."""
    now = ctx.clock.now()
    if lease.pid is not None:
        ctx.process.kill(lease.pid)  # best-effort hygiene; the epoch fence is the guarantee

    # attempt_count includes this lease (its context row was written at mint); the
    # first attempt is not a retry, so retries-so-far is one less.
    retried = ctx.store.attempt_count(lease.chunk_id, lease.node_id) - 1
    if retried < lease.retries_max:
        ctx.store.record_closure(
            lease_id=lease.lease_id, chunk_id=lease.chunk_id, node_id=lease.node_id, reason=reason, closed_at=now
        )
        _requeue(ctx, lease)
        return
    if _reassigned_or_detached(ctx, lease):
        _abandon_reassigned(ctx, lease, killed=True, via=via)
        return
    if ctx.store.local_paused(ctx.config.runner_id):
        _log.info(
            "escalation deferred — locally paused",
            runner_id=ctx.config.runner_id,
            via=via,
            chunk_id=lease.chunk_id,
            lease_id=lease.lease_id,
        )
        return
    ctx.store.record_closure(
        lease_id=lease.lease_id, chunk_id=lease.chunk_id, node_id=lease.node_id, reason=_ESCALATED, closed_at=now
    )
    _escalate(ctx, lease)


def _adopt_interrupted_claim(ctx: LoopContext, chunk_id: str) -> None:
    """Spawn the current node for a claimed chunk whose FILL crashed before the lease minted.

    The hub confirms this runner holds the route (D-080) and the runner holds the binding,
    but no lease was ever minted (the crash landed in FILL's claim→spawn window). Recovery
    is a spawn of the chunk's current node from its idempotent envelope (D-090) into the
    already-bound environment — the same work FILL's tail would have done.

    A 404 (:class:`ChunkNotFoundError`) here is terminal the same way it is for
    :func:`_advance_held_chunk` (blizzard#9): there is no active lease over this chunk to
    reap, only the binding this function already owns, so a chunk the hub no longer knows
    about is released the same way rather than retried forever."""
    bindings = ctx.store.bindings_for_chunk(chunk_id)
    if not bindings:
        _log.warning("adopt with no bound env — cannot spawn", chunk_id=chunk_id)
        return
    try:
        envelope = ctx.hub.get_envelope(chunk_id)
    except ChunkNotFoundError:
        _log.warning("hub reports adopted chunk unknown — releasing envs", chunk_id=chunk_id)
        _release_all(ctx, chunk_id)
        return
    except HubClientError:
        return  # hub unreachable — the binding is durable; retry next tick
    _log.info("adopting interrupted claim — spawning current node", chunk_id=chunk_id)
    _spawn_attempt(ctx, chunk_id, envelope, _bindings_as_environments(bindings), via="adopt")


def _reclaim_interrupted(ctx: LoopContext, chunk_id: str, bindings: list[EnvBindingRecord]) -> None:
    """Complete a claim whose hub POST never landed — claim now, reusing the held binding.

    The runner bound the chunk's environment but crashed before (or during) the claim, so
    the hub still shows the chunk ``ready``. Rather than release and re-acquire (which would
    churn the environment and re-bind the same id), the runner claims the route with the
    environment it already holds and spawns on success; a 409 means another runner took the
    chunk while this one was down, so the binding is released (D-080)."""
    envs = _bindings_as_environments(bindings)
    claim = RouteClaim(
        chunk_id=chunk_id,
        runner_id=ctx.config.runner_id,
        workspace_id=ctx.config.workspace_id,
        environment_ids=[b.environment_id for b in bindings],
    )
    try:
        outcome = ctx.hub.claim_route(claim)
    except HubClientError:
        return  # hub unreachable — the binding is durable; retry next tick
    if outcome.conflict is not None or outcome.claimed is None:
        _log.info("interrupted claim lost the race — releasing binding", chunk_id=chunk_id)
        _release_all(ctx, chunk_id)
        return
    _log.info("re-claimed interrupted chunk — spawning current node", chunk_id=chunk_id)
    _spawn_attempt(ctx, chunk_id, outcome.claimed.envelope, envs, via="reclaim")


def _requeue(ctx: LoopContext, lease: LeaseRecord) -> None:
    """Re-attempt the node in the same environments — new session, new lease, fresh epoch (D-082).

    The prior attempt's lease is already closed by the caller (:func:`_fail_attempt`) before
    this runs, so a 404 (:class:`ChunkNotFoundError`) here leaves no active lease behind for
    PULL's own sweep (:func:`_release_detached`) to find and clean up — reached, notably, from
    REAP, which precedes PULL in tick order, so PULL's sweep has not yet run this same chunk
    this tick. Left as a generic :class:`HubClientError`, this is the same held-forever shape
    issue #9 fixed for :func:`_reassigned_or_detached`, just for a chunk gone between the
    failed attempt and its requeue rather than between two ticks — so it is released here too."""
    bindings = ctx.store.bindings_for_chunk(lease.chunk_id)
    if not bindings:
        _log.warning("requeue with no bound env — cannot re-spawn", chunk_id=lease.chunk_id)
        return
    try:
        envelope = ctx.hub.get_envelope(lease.chunk_id)  # idempotent re-read (D-090)
    except ChunkNotFoundError:
        _log.warning("hub reports chunk unknown at requeue — releasing envs", chunk_id=lease.chunk_id)
        _release_all(ctx, lease.chunk_id)
        return
    except HubClientError:
        return  # the closed attempt is durable; FILL/ADVANCE re-drives next tick
    _log.info("requeuing at node", chunk_id=lease.chunk_id, node=lease.node_name)
    _spawn_attempt(ctx, lease.chunk_id, envelope, _bindings_as_environments(bindings), via="requeue")


def _escalate(ctx: LoopContext, lease: LeaseRecord) -> None:
    """Park the chunk needs-human at the hub, envs held for takeover (D-009/D-083).

    The escalation rides the outbound buffer as an ``escalation.recorded`` fact,
    flushed to the hub's POST /events (D-069), where the fleet derives ``needs_human``
    (an open escalation with no later lease mint — domain/events.md). It carries the
    pasteable takeover command — ``cd <workdir> && <harness resume>`` composed from the
    adapter's session surface (design/harness-adapters.md) — so a human resumes the
    parked session in the agent's own warm worktrees; a requeue's later lease mint
    closes it by supersession (D-067). Environments stay bound throughout.
    """
    now = ctx.clock.now()
    bindings = ctx.store.bindings_for_chunk(lease.chunk_id)
    takeover = ""
    if lease.session_id is not None and bindings:
        takeover = ctx.harness.resume_command(bindings[0].workdir, lease.session_id)
    payload = json.dumps({"chunk_id": lease.chunk_id, "epoch": lease.epoch, "takeover_command": takeover})
    ctx.store.enqueue_outbound(
        kind=ESCALATION_RECORDED, chunk_id=lease.chunk_id, lease_id=lease.lease_id, payload=payload, created_at=now
    )
    _log.info("escalated to needs-human — retries exhausted", chunk_id=lease.chunk_id, takeover=takeover)


def _park_on_ask(ctx: LoopContext, lease: LeaseRecord, ask: AskRecord) -> None:
    """Park the chunk on a question: forward it to the hub and stop the reap clock.

    The worker asked and exited, so there is no live worker to judge or reap ([ask-
    answer.md]): the question rides the outbound buffer up to the hub (store-and-forward,
    D-069), where it becomes the durable row the chunk derives ``waiting_on_human`` from,
    and the local park fact keeps REAP off the dormant lease and ADVANCE from re-parking
    or eliciting a verdict. The env bindings stay held (D-083) so the session is warm for
    the resume. No retry is consumed — a park is not a failed attempt (D-009).
    """
    now = ctx.clock.now()
    payload = json.dumps(
        {
            "question_id": ask.question_id,
            "chunk_id": lease.chunk_id,
            "node_id": lease.node_id,
            "session_id": ask.session_id or lease.session_id,
            "epoch": lease.epoch,
            "question": ask.question,
            "options": ask.options,
            "asked_at": iso_utc(ask.asked_at),
        }
    )
    ctx.store.enqueue_outbound(
        kind=QUESTION_ASKED, chunk_id=lease.chunk_id, lease_id=lease.lease_id, payload=payload, created_at=now
    )
    ctx.store.record_park(lease_id=lease.lease_id, chunk_id=lease.chunk_id, question_id=ask.question_id, parked_at=now)
    _log.info("chunk parked on question", chunk_id=lease.chunk_id, question_id=ask.question_id)


def _resume_if_answered(ctx: LoopContext, lease: LeaseRecord) -> None:
    """Poll a parked lease's question; on an answer, resume the dormant session (D-050).

    The answer is a durable row at the hub, so this is crash-safe and re-runnable: while
    the question is unanswered the poll is a no-op and the reap clock stays stopped. Once
    answered, the agent is **reconstituted around the answer** — the same session, same
    lease, same node-step (D-082) — via the adapter's resume-with-message. The lease's
    new pid is recorded so it reads live again, the park is closed, and ``answer.delivered``
    is buffered up to the hub (board detail; the status already flipped at question.answered).

    Gated by the local brake (issue #45) **before the poll** — a paused runner makes no hub
    poll at all. A suppressed resume leaves the park open; the answer is picked up once the
    brake clears (no retry consumed either way, D-009).
    """
    if _spawn_suppressed(ctx, via="answer-resume", chunk_id=lease.chunk_id, lease_id=lease.lease_id):
        return
    park = ctx.store.open_park(lease.lease_id)
    if park is None:
        return  # not actually parked (raced with a resume)
    try:
        question = ctx.hub.get_question(park.question_id)
    except HubClientError:
        return  # hub unreachable — the park is durable; retry next tick
    if not question.answered or question.answer is None:
        return  # still waiting — reap clock stays stopped
    bindings = ctx.store.bindings_for_chunk(lease.chunk_id)
    if not bindings:
        _log.warning("answered park with no bound env — cannot resume", chunk_id=lease.chunk_id)
        return

    # The resume prompt reconstitutes the agent around the answer ([ask-answer.md]). The
    # human framing rides a leading comment line and the answer itself is the payload, so
    # the agent reads "who answered" as context and acts on the answer body — a shape the
    # blizzard-mock façade (prompt-is-program) executes directly, and a real harness reads
    # as ordinary resume text (the exact prose is unpinned, D-061).
    who = question.answered_by or "operator"
    message = f"# Answer from {who}. Continue.\n{question.answer}"
    pid = ctx.harness.resume_with_message(bindings[0].workdir, lease.session_id or "", message)
    now = ctx.clock.now()
    # The resumed worker runs under the same lease and session; record its new pid so the
    # lease reads live again (REAP/ADVANCE treat it as any running worker from here).
    ctx.store.record_spawn(
        lease.lease_id,
        pid=pid,
        process_start_time=ctx.process.start_time(pid) or "",
        session_id=lease.session_id or "",
        spawned_at=now,
    )
    ctx.store.record_park_resume(lease_id=lease.lease_id, question_id=park.question_id, resumed_at=now)
    ctx.store.enqueue_outbound(
        kind=ANSWER_DELIVERED,
        chunk_id=lease.chunk_id,
        lease_id=lease.lease_id,
        payload=json.dumps({"chunk_id": lease.chunk_id, "question_id": park.question_id}),
        created_at=now,
    )
    _log.info("resumed dormant session with answer", chunk_id=lease.chunk_id, question_id=park.question_id, pid=pid)


def _collect_asset_artifacts(
    envelope: NodeEnvelope, git_artifacts: list[SubmittedArtifact], assessment: str
) -> list[SubmittedArtifact]:
    """Emit an asset artifact per produced name no git commit covers (D-026).

    The engine has no file convention for assets (D-056): a node that declares it
    ``produces`` a name — the review node's ``findings`` — but pushes no git commit of
    that name emits the worker's judgement assessment as the asset's content. Git-commit
    artifacts are named by repo, so a build node producing repo commits yields no
    assets; a read-only review node yields its findings. Content may be empty (a clean
    pass) — the asset still lands, and only a fail routes it back into build (latest-by-epoch)."""
    from blizzard.hub.domain.artifacts import ArtifactKind

    covered = {a.name for a in git_artifacts}
    return [
        SubmittedArtifact(name=name, kind=ArtifactKind.ASSET, content=assessment)
        for name in envelope.node.produces
        if name not in covered
    ]


def _push_and_collect_artifacts(ctx: LoopContext, bindings: list[EnvBindingRecord]) -> list[SubmittedArtifact]:
    """Discover the produced git commits, push their branches, and name them (D-026)."""
    from blizzard.hub.domain.artifacts import ArtifactKind

    submitted: list[SubmittedArtifact] = []
    for binding in bindings:
        for produced in ctx.worktree_git.find_produced_artifacts(binding.workdir, ctx.config.base_branch):
            ctx.worktree_git.push(produced.repo_workdir, produced.branch_name)
            submitted.append(
                SubmittedArtifact(
                    name=produced.repo,
                    kind=ArtifactKind.GIT_COMMIT,
                    repo=produced.repo,
                    branch_name=produced.branch_name,
                    commit_hash=produced.commit_hash,
                )
            )
    return submitted


def _release_all(ctx: LoopContext, chunk_id: str) -> None:
    """Release every held environment at the chunk's tenure end (D-083)."""
    now = ctx.clock.now()
    for binding in ctx.store.bindings_for_chunk(chunk_id):
        ctx.provider.release(binding.environment_id)
        ctx.store.record_release(chunk_id=chunk_id, environment_id=binding.environment_id, released_at=now)


def _release_acquired(ctx: LoopContext, acquired: list[AcquiredEnvironment]) -> None:
    """Release just-acquired (unbound) environments after a lost claim (D-080)."""
    for a in acquired:
        ctx.provider.release(a.environment_id)


def _release_binding(ctx: LoopContext, chunk_id: str, acquired: list[AcquiredEnvironment]) -> None:
    """Undo a just-recorded binding whose claim never landed — release the fact and the env.

    The binding is written before the hub claim (D-083), so a claim that fails to send or
    loses the race must retract both the local binding fact and the provider allocation,
    leaving the chunk exactly as if it had never been touched (it stays ``ready``)."""
    now = ctx.clock.now()
    for a in acquired:
        ctx.store.record_release(chunk_id=chunk_id, environment_id=a.environment_id, released_at=now)
        ctx.provider.release(a.environment_id)


def _bindings_as_environments(bindings: list[EnvBindingRecord]) -> list[AcquiredEnvironment]:
    return [AcquiredEnvironment(environment_id=b.environment_id, workdir=b.workdir) for b in bindings]


def _elicitation_tail(envelope: NodeEnvelope) -> str:
    """The engine-generated ``<Choice>`` elicitation appended to the judgement prompt (D-042).

    Emitted as ``#``-prefixed lines so the tail is harness-agnostic: inert whether
    the judgement prompt is LLM prose (a comment block a real coding harness still
    reads) or a mock behavior *script* (the mock ``exec``s the prompt, and a bare
    prose tail would be a ``SyntaxError``).
    """
    lines = ["", "", "# Select exactly one outcome and reply with <Choice>name</Choice>:"]
    for choice in envelope.node.choices:
        lines.append(f"#   - {choice.name}: {choice.description}")
    return "\n".join(lines)
