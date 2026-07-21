"""The reconciliation step functions — REAP → PULL → FILL → ADVANCE (``bzh:steppable-loop``).

Each is an individually callable function of a :class:`LoopContext`; the tick driver
and the ``blizzard runner tick`` CLI verb call them in order. Every step is
idempotent and holds no state of its own — all facts live in the runner store, so a
crash mid-tick followed by a restart re-runs the tick harmlessly, and
startup recovery is just REAP running first.

The dead-worker split: a **session-bearing** worker whose
process has *exited* is a *done declaration* (exit-is-done) and belongs to
ADVANCE — its judgement reply, or its absence, tells a done from a crash.
REAP handles the residue ADVANCE structurally cannot judge: a lease whose worker
never reached spawn-return (no pid/session — killed mid-FILL), and a **stalled-but-
alive** worker whose heartbeat has gone stale (a live pid that stopped making tool
calls, so it stopped beating). Both the verdict-less-exit failure (ADVANCE)
and the reaped orphan/stall (REAP) route through one ``requeue-or-escalate`` decision
keyed on the node's retry budget. Liveness is heartbeat-freshness for a
live pid, plus (pid, start_time) to survive pid reuse.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import uuid
from datetime import datetime, timedelta

from blizzard.foundation.crash import crashpoint
from blizzard.foundation.ids import LEASE_PREFIX, mint
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.domain.enrollment import hash_token
from blizzard.hub.domain.work import ChunkStatus
from blizzard.runner.domain.leases import as_utc, is_heartbeat_stale
from blizzard.runner.environments.provider import (
    AcquiredEnvironment,
    EnvironmentPreparationError,
    WorkspaceAcquisitionError,
)
from blizzard.runner.harness.adapter import WorkerPreamble
from blizzard.runner.harness.preamble import render_worker_preamble
from blizzard.runner.harness.spawn_cwd import resolve_spawn_cwd
from blizzard.runner.harness.usage import UsageKind, UsageSample
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
from blizzard.wire.completion import CompletionSubmission, SubmittedArtifact, satisfied_produces_names
from blizzard.wire.decision import DecisionSubmission, DecisionView
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse, NodeEnvelope
from blizzard.wire.facts import (
    ANSWER_DELIVERED,
    ESCALATION_RECORDED,
    LEASE_MINTED,
    QUESTION_ASKED,
    RUNNER_LOCALLY_PAUSED,
    RunnerFact,
    RunnerFactBatch,
)
from blizzard.wire.route import RouteClaim

#: This module's public API — the loop steps it owns. ``HEARTBEAT_STALENESS_THRESHOLD``
#: lives in ``runner/domain/leases.py`` (its one owner, ``bzh:domain-core``); this
#: module no longer re-exports it — importers (tests included) reach it there.
__all__ = [
    "advance",
    "check_spend_ceiling",
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
_RELEASED = "released"  # the chunk was found reassigned/detached/unknown — abandon, no requeue (blizzard#9)

#: The message RESUME delivers into a marked session on a restart. Framed
#: as a ``#``-prefixed comment so it is inert whether the session is real-harness prose or a
#: blizzard-mock behavior *script* it ``exec``s (the same convention as the elicitation tail
#: and the answer-resume framing). The exact prose is unpinned.
_RESTART_RESUME_MESSAGE = "# The supervisor restarted; continue your task where you left off."

#: The message ADVANCE delivers into a session the operator paused and then resumed (issue #46).
#: Same ``#``-prefixed inert-comment framing as the restart resume above, for the same reason: the
#: session may be real-harness prose or a blizzard-mock behavior *script* it ``exec``s. The exact
#: prose is unpinned.
_PAUSE_RESUME_MESSAGE = "# The operator resumed this chunk; continue your task where you left off."

# Outbound-buffer fact kinds. ``completion.submitted`` is the
# runner-local kind whose flush drives the apply-response; ``decision.submitted`` is the
# runner-config gate's kind, which parks the chunk instead of advancing it; the
# two hub-fact kinds (LEASE_MINTED / ESCALATION_RECORDED) flush to POST /events.
_COMPLETION_KIND = "completion.submitted"
_DECISION_KIND = "decision.submitted"

# The env count a solo chunk wants; batching (K>1) is parked.
_SOLO_ENV_COUNT = 1

# The lease capability token's byte length (issue #113, Phase 1) — mirrors the
# hub's own route-token mint (`hub/domain/claim.py`'s `_ROUTE_TOKEN_BYTES`).
_LEASE_TOKEN_BYTES = 32

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
# reassigned/detached while the runner was down), PULL's `_reconcile_leases` (reassigned/detached
# while the runner was up, caught by its live-tick detach check), or REAP's `_fail_attempt` escalate
# guard (an exhausted-retries lease the hub already moved elsewhere, since blizzard#38). A crash
# here leaves a lease with a dead pid, environments not yet released, and no closure recorded, so
# the lease is still active at the next startup. That next tick's recovery differs by how the
# lease got here: a lease `mark_crash_resume_intents` marks for resume — session-bearing, not
# parked/pending-submission/session-ended, and not stale-heartbeat as measured at crash time —
# is re-asked by RESUME, finds it still not ours, and re-runs this same abandon idempotently. A
# lease in one of those skipped states gets no resume intent, so RESUME never revisits it — but
# PULL's own `_reconcile_leases` re-scans *every* active lease each tick, unconditional on those
# states, and reaches the identical re-ask; it is the stronger recovery story of the two, and the
# one that actually covers every path into this function (killing an already-dead pid is a
# no-op; `_release_all` and `record_closure` are re-runnable), and the chunk lands exactly once.
_CP_ABANDON_AFTER_KILL = crashpoint(
    "abandon.after-kill.before-release", "detached worker killed; environments not yet released"
)

# PAUSE — the operator's per-chunk pause park (`_kill_and_park_paused`, issue #46), reached from
# RESUME (a chunk paused while the runner was down) and PULL's `_reconcile_leases` (paused while
# it was up). Its own boundary family, not `abandon.*`'s and not a step's: this is the deliberate
# *inverse* of the abandon — the worker dies but the claim, the route, the epoch and every
# environment survive — and it is reached from two different steps, so naming it for either one
# would be false. A crash here leaves a lease that is still active, session-bearing, pid dead, and
# NOT yet parked. Recovery converges *because of* the RESUME fix (`_resume_marked_lease`): startup
# crash-recovery marks that exact shape for resume (fresh-at-crash heartbeat, no session-end),
# RESUME re-asks the hub, reads `detail.pause is not None`, and re-runs this same park idempotently
# — killing the already-dead pid is a no-op. Before that fix the identical path *abandoned* the
# chunk, so this point is the regression fence on the plan's central bug, not decoration.
_CP_PAUSE_PARK_AFTER_KILL = crashpoint(
    "pause.after-kill.before-park", "paused worker killed; pause-park not yet durable"
)

# PULL — the single outbound flusher (store-and-forward drain).
_CP_PULL_BEFORE = crashpoint("pull.before-flush", "entered PULL; registry synced, buffer not drained")
_CP_PULL_AFTER = crashpoint("pull.after-flush", "PULL done; buffer drained as far as it could")

# FILL — peek -> acquire -> BIND -> claim -> spawn. The local binding is
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
# Usage recording (issue #58) sits between the verdict and the completion buffer: a crash
# here either finds this attempt's usage facts already durable (idempotent re-run, keyed
# on lease/generation/kind) or reaches neither them nor the completion — never a
# double-count. Named for the window it opens, not the step whose call site reaches it
# (``bzh:crash-point-registry``).
_CP_ADV_AFTER_USAGE = crashpoint("advance.after-usage.before-buffer", "usage facts recorded; completion not buffered")

# ADVANCE's nudge-once (issue #113, Phase 4): a `produces` name with neither a git
# commit nor an attachment gets one resumed nudge, gated on a durable
# `(lease, epoch)` fact recorded BEFORE the resume runs (see the comment at the call
# site for why that ordering, not the reverse, is the one that makes "at most one
# nudge" hold across a crash at either point). `after-fired-fact` is reached the
# instant that guard is durable and before the resume it guards has run at all: a
# crash here must never re-nudge (the fact alone already forbids it) and must not
# assume the worker ever saw the message. `after-resume` is reached once the resume
# has returned and before attachments are re-read / the completion reassembled: a
# crash here finds the fact already durable (no re-nudge possible) and recovery's own
# fresh re-evaluation of the missing set — sourced from the same durable attachments
# table a restarted ADVANCE always re-reads — picks up whatever the worker attached,
# or falls back to the assessment for what it didn't, exactly as an unnudged pass
# would.
_CP_NUDGE_AFTER_FIRED_FACT = crashpoint(
    "nudge.after-fired-fact.before-resume",
    "nudge-fired fact durable; the resume that delivers the nudge has not run yet",
)
_CP_NUDGE_AFTER_RESUME = crashpoint(
    "nudge.after-resume.before-reassemble",
    "nudge resume returned; attachments not yet re-read and the completion not yet reassembled",
)

_CP_ADV_AFTER_BUFFER = crashpoint("advance.after-buffer.before-flush", "completion buffered; not yet flushed")

# The between-attempts step boundary the per-chunk spend cap checks at (issue #61a), inside
# the flush's apply-response consumption: the prior attempt's closure is already durable
# (reason=transitioned — it genuinely completed) when this is reached, and neither the cap
# check (a hub read) nor its outcome (park via `_escalate`, or spawn the next attempt) has
# happened yet. A crash here leaves exactly that shape — a chunk with no active lease, no
# escalation, no next lease — which FILL's `_reconcile_interrupted_claims` already recovers
# by adopting (spawning) the chunk's current node the same as any other interrupted-claim
# window; the recovered attempt re-reaches this same boundary and is checked again.
_CP_ADV_AFTER_CLOSURE = crashpoint(
    "advance.after-closure.before-cost-cap-check", "attempt closed; cap check and next-step decision not yet made"
)

# FLUSH (of the buffered completion, inside PULL) — submit -> ack -> apply-response. The
# after-submit.before-ack window is the lost-ack replay the hub's idempotency must absorb.
_CP_FLUSH_BEFORE_SUBMIT = crashpoint("flush.before-submit", "completion at head of buffer; not submitted")
_CP_FLUSH_AFTER_SUBMIT = crashpoint("flush.after-submit.before-ack", "hub applied the completion; ack not recorded")
_CP_FLUSH_AFTER_ACK = crashpoint("flush.after-ack.before-apply-response", "ack recorded; apply-response not consumed")
_CP_FLUSH_AFTER_APPLY = crashpoint("flush.after-apply-response", "apply-response consumed; chunk continued in place")


# --------------------------------------------------------------------------- #
# Usage telemetry (issue #58) — the per-lease stdout redirect and its readback.
# --------------------------------------------------------------------------- #


def _stdout_path(ctx: LoopContext, lease_id: str, generation: int) -> str:
    """This lease's per-generation harness-stdout redirect target, or ``""`` for no
    redirect.

    Empty when ``worker_stdout_dir`` is unset (Phase 1's discard/inherit default, and
    every test that does not wire one) — the composition root (``loop/build.py``)
    resolves the real directory and creates it once. Scoped to ``(lease_id,
    generation)``, not just ``lease_id``: each spawn/resume gets its own file, so
    ADVANCE's readback for a given attempt (:func:`_worker_usage_sample`) sees only
    that attempt's own envelope line, never a prior generation's — a generation whose
    own invocation exited without writing one correctly falls through to the
    transcript-sum fallback instead of replaying a stale envelope (the bug this
    per-generation split fixes). The adapter still opens the file in append mode, so a
    retry that lands before this attempt's own ``record_spawn`` is durable (the
    un-armable spawn-record gap every resume site's docstring calls out, e.g.
    :func:`_resume_in_place`) safely reuses the same generation number and the same
    file rather than colliding with a different attempt's."""
    if not ctx.config.worker_stdout_dir:
        return ""
    return os.path.join(ctx.config.worker_stdout_dir, f"{lease_id}.{generation}.stdout")


def _pending_generation(ctx: LoopContext, lease_id: str) -> int:
    """The spawn generation this lease's next spawn/resume is about to mint — one past
    :meth:`~blizzard.runner.store.repository.IReadRunnerStore.lease_generation`'s
    durably-recorded count, read *before* this attempt's own ``record_spawn`` call
    lands. Every write call site (:func:`_spawn_attempt`, and each resume family
    member) reads this to name its own :func:`_stdout_path` ahead of that call."""
    return ctx.store.lease_generation(lease_id) + 1


def _read_stdout(path: str) -> str:
    """The per-lease stdout file's full text, or ``""`` when absent/unreadable.

    Never raises: a missing file (nothing was ever redirected, or it was already
    cleaned up at release) is the ordinary "no envelope" case the caller falls back
    from, not a fault to log."""
    if not path:
        return ""
    try:
        with open(path, "rb") as f:
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _record_worker_usage(ctx: LoopContext, lease: LeaseRecord, bindings: list[EnvBindingRecord]) -> None:
    """Record just this attempt's spawn/resume invocation usage — no judgement ran.

    The token-burning invocation whose exit ADVANCE is handling, attributed to this
    lease's current spawn generation (``spawn`` on generation 1, ``resume`` after —
    issue #58, reusing issue #13's generation tracking). Every exit path through
    ADVANCE that burned a spawn/resume invocation records this, whether or not a
    judgement follows: a worker that asked-and-exited (:func:`_park_on_ask`) elicited
    no verdict, so it records this alone; the judged paths add the judge fact on top
    (:func:`_record_attempt_usage`). Keyed on ``(lease, generation, kind)`` it is
    idempotent across a re-run and distinct from the *next* generation's resume fact,
    so an ask-park's spawn usage and its later answer-resume usage never collide.
    """
    generation = ctx.store.lease_generation(lease.lease_id)
    kind: UsageKind = "spawn" if generation <= 1 else "resume"
    worker_sample = _worker_usage_sample(ctx, lease, bindings, generation=generation, kind=kind)
    if worker_sample is not None:
        _store_usage(ctx, lease, generation=generation, sample=worker_sample)


def _record_attempt_usage(
    ctx: LoopContext, lease: LeaseRecord, bindings: list[EnvBindingRecord], *, judge_output: str
) -> None:
    """Record this attempt's harness usage: the spawn/resume invocation whose exit
    ADVANCE is judging, and the judgement resume that elicited its verdict — each its
    own fact, keyed on this lease's current spawn generation (issue #58, reusing issue
    #13's own generation tracking). Called just before the completion is buffered
    (``_CP_ADV_AFTER_USAGE``) and equally on the verdict-less-fail exit (both burned the
    judge invocation): a crash between the two either finds these facts already durable
    — idempotent re-run, keyed on ``(lease, generation, kind)`` — or reaches neither,
    never a double-count.
    """
    _record_worker_usage(ctx, lease, bindings)
    generation = ctx.store.lease_generation(lease.lease_id)
    judge_sample = ctx.harness.parse_usage(judge_output, "judge")
    if judge_sample is not None:
        _store_usage(ctx, lease, generation=generation, sample=judge_sample)


def _store_usage(ctx: LoopContext, lease: LeaseRecord, *, generation: int, sample: UsageSample) -> None:
    ctx.store.record_usage(
        lease_id=lease.lease_id,
        chunk_id=lease.chunk_id,
        node_id=lease.node_id,
        epoch=lease.epoch,
        generation=generation,
        sample=sample,
        recorded_at=ctx.clock.now(),
    )


def _worker_usage_sample(
    ctx: LoopContext, lease: LeaseRecord, bindings: list[EnvBindingRecord], *, generation: int, kind: UsageKind
) -> UsageSample | None:
    """This attempt's own spawn/resume usage: parsed off *this generation's own*
    stdout file's result envelope, falling back to a transcript-summed, cost-absent
    sample when no envelope survived — the worker was killed or reaped before it ever
    wrote one (never fabricated: no envelope and no transcript is simply no fact).
    Scoped to ``generation`` (not just the lease), so a generation whose own
    invocation wrote no envelope of its own can never read back a *prior*
    generation's — see :func:`_stdout_path`."""
    output = _read_stdout(_stdout_path(ctx, lease.lease_id, generation))
    sample = ctx.harness.parse_usage(output, kind) if output else None
    if sample is not None:
        return sample
    if ctx.transcripts is None or lease.session_id is None:
        return None
    fallback_workdir = bindings[0].workdir if bindings else None
    spawn_cwd = resolve_spawn_cwd(ctx.config.workspace_root, fallback_workdir)
    lines = ctx.transcripts.read_raw_lines(lease.session_id, spawn_cwd=spawn_cwd)
    if not lines:
        return None
    return ctx.harness.sum_transcript_usage(lines, kind)


# --------------------------------------------------------------------------- #
# Runner spend ceiling (issue #61b) — the tick-level kill-switch, first in the tick.
# --------------------------------------------------------------------------- #


def check_spend_ceiling(ctx: LoopContext) -> None:
    """Engage the local pause brake once this runner's rolling-window spend reaches
    ``cost.runner_ceiling_usd`` — the runner-wide counterpart to :func:`_park_on_cost_cap`'s
    per-chunk cap, sharing the ``[cost]`` table and its identical lower-bound + PARTIAL
    cost-absent treatment.

    Runs **first** in the tick (:func:`blizzard.runner.loop.tick.tick`, ahead of REAP,
    RESUME, PULL, FILL and ADVANCE) so a crossing detected this tick is already visible to
    every spawn primitive gated by :func:`_spawn_suppressed` and to REAP's kill-a-stalled-
    worker deferral, within the *same* pass — no worker is newly spawned, and no live
    worker is killed, on the strength of a check that ran too late in its own tick.

    Reuses the existing local pause brake rather than inventing a second suppression
    mechanism (the locked design, issue #61): the exact ``record_local_pause`` call
    ``blizzard runner pause`` itself makes, so every existing spawn-suppression site
    already honors it and no retry budget is touched. Reads ``local_paused`` first and
    returns immediately when already engaged — engaging is a one-time transition, not a
    per-tick assertion, so a runner already paused (by this ceiling or by an operator's own
    ``blizzard runner pause``) is left alone rather than re-escalated on every later tick,
    even while the rolling window's sum stays over the ceiling for as long as it holds.
    **No auto-unpause**: this function never calls ``record_local_pause(paused=False,
    ...)`` — ``blizzard runner start`` is the only conscious clear, and the brake does not
    lift itself when the window later rolls the spend back under the ceiling.

    ``cost.runner_ceiling_usd`` absent means no ceiling — unchanged pre-#61b behavior. The
    window is summed **locally** (unlike the per-chunk cap's hub-derived read): this
    runner's own :meth:`~blizzard.runner.store.repository.IReadRunnerStore.usage_since`
    over the trailing ``runner_ceiling_window_hours``, off the injected clock, never wall
    time, so a timezone or DST change never moves the boundary.

    Crash safety: the only durable write here is the single-transaction
    ``record_local_pause`` (local pause fact + its hub-bound report, atomic by
    construction — the same call the manual pause route makes, which carries no crash
    point of its own for the same reason). Everything before it (``local_paused``,
    ``usage_since``) is a plain read with no observable partial state, so a crash at any
    point up to the write leaves nothing to recover: the next tick simply re-derives the
    identical decision from the same durable facts and the (now later) clock. This opens
    no new crash window, so no new crash-point-registry point is added.
    """
    cap = ctx.config.runner_ceiling_usd
    if cap is None:
        return
    if ctx.store.local_paused(ctx.config.runner_id):
        return  # already engaged — engage-once; `blizzard runner start` is the only clear
    now = ctx.clock.now()
    since = now - timedelta(hours=ctx.config.runner_ceiling_window_hours)
    totals = ctx.store.usage_since(since)
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
    ctx.store.record_local_pause(
        ctx.config.runner_id,
        paused=True,
        at=now,
        by="runner-ceiling",
        report_kind=RUNNER_LOCALLY_PAUSED,
        report_payload=json.dumps(
            {"runner_id": ctx.config.runner_id, "by": "runner-ceiling", "at": iso_utc(now), "reason": reason}
        ),
    )


# --------------------------------------------------------------------------- #
# REAP
# --------------------------------------------------------------------------- #


def reap(ctx: LoopContext) -> None:
    """Expire leases whose worker is gone or **stalled**.

    Three cases end an attempt here (each a failed execution attempt —
    requeue or escalate):

    * **orphan** — a lease with no recorded pid/session: minted at FILL but never
      spawned (a crash in the mint→spawn window). ADVANCE structurally cannot judge it.
    * **stalled-but-alive** — a live worker whose last heartbeat is older than the
      conservative :data:`HEARTBEAT_STALENESS_THRESHOLD`. Heartbeats ride tool calls,
      so a worker that stops progressing stops beating; there is no separate
      stall detector. REAP kills it (``_fail_attempt`` does the best-effort kill) — the
      epoch fence, not the kill, is what guarantees the zombie cannot deliver.

    A session-bearing worker whose process has **exited** is *not* reaped here: exit is
    the done declaration, so it belongs to ADVANCE, which resumes the session
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

    **A chunk under an open takeover (issue #52) is skipped outright**, ahead of every
    other case: the human already holds the session (a forced takeover already killed
    and closed it; a non-forced one only ever takes a dormant lease already excluded by
    the ``parked`` check below), so this is defense-in-depth, not the primary guard —
    but it is what keeps REAP off a chunk the moment a takeover opens, with no
    dependency on which shape the park was.
    """
    _CP_REAP_BEFORE.reached()
    local_paused = ctx.store.local_paused(ctx.config.runner_id)
    now = ctx.clock.now()
    parked = ctx.store.parked_lease_ids()
    taken_over = ctx.store.open_takeover_chunk_ids()
    deferred = 0
    for lease in ctx.store.list_active_leases():
        if lease.chunk_id in taken_over:
            continue  # the human holds this session — no loop step touches it
        if lease.lease_id in parked:
            # Dormant on a question (ask-and-exit): no live worker to stall, so the
            # reap clock is stopped — a parked chunk is never reaped for inactivity.
            # The answer's arrival resumes it (ADVANCE).
            continue
        if lease.pid is None or lease.session_id is None:
            _log.info("reaping unspawned lease", lease_id=lease.lease_id, chunk_id=lease.chunk_id)
            _fail_attempt(ctx, lease, reason=_REAPED, via="reap")
            continue
        if not ctx.process.is_alive(lease.pid, lease.process_start_time or ""):
            continue  # exited — ADVANCE's (exit-is-done)
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
# RESUME — the restart re-attach: graceful marking (#12) + crash detection (#13)
# --------------------------------------------------------------------------- #


def mark_resume_intents(store: IWriteRunnerStore, *, now: datetime) -> int:
    """Mark every in-flight lease for same-lease restart-resume — the graceful-shutdown hook.

    Called once as the daemon exits gracefully (SIGTERM: ``systemctl restart``/stop), *before*
    the workers die, so the next startup's :func:`resume` re-attaches each in-flight session in
    place instead of retrying it fresh. An ungraceful ``kill -9`` never runs this — that case is
    recovered symmetrically by :func:`mark_crash_resume_intents`, which ``host`` runs at startup
    to mark the same intent for a lease killed mid-work (#13), so both restart paths converge on
    the one RESUME step.

    Marks an **active, non-parked, session-bearing** lease: a parked lease is dormant on a
    question (its own resume is the answer); a lease with a pending completion/
    decision has its verdict already elicited (its node-step is done, awaiting flush); a
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
    """Detect crash-orphaned sessions at startup and mark them for same-lease resume (#13).

    The **ungraceful** sibling of :func:`mark_resume_intents`. A ``kill -9`` / OOM / reboot
    never runs the graceful shutdown marker, so the next startup has to find the interrupted
    sessions itself and route them to the *same* RESUME re-attach — instead of ADVANCE reading
    each dead worker as a done declaration and failing it verdict-less into a fresh
    retry, discarding its accumulated context exactly when recovery should keep it.
    Run once by the ``host`` command before the loop starts, symmetric with the graceful marker
    in its shutdown ``finally``; the first tick's :func:`resume` then consumes the marks, so the
    ungraceful path reuses every fence the graceful one already carries — kill-first, the
    unchanged epoch, and the abandon-if-reassigned ownership check.

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
            continue  # declared done (SessionEnd fired) — ADVANCE judges it (exit-is-done)
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
    """Re-attach to in-flight sessions a restart marked for same-lease resume — startup recovery.

    A no-op on every normal tick (nothing is marked); non-empty only on the first tick after a
    restart, whether marked by the graceful shutdown hook (#12) or by ``host``'s startup crash-
    recovery scan when a ``kill -9`` / reboot skipped that hook (#13). Both write the same
    resume-intent, so this step is indifferent to which; each marked lease is either **resumed in
    place** — under the unchanged
    ``lease_id``/``epoch``/``session_id``, only ``pid``/``process_start_time`` rewritten, no retry
    consumed — or, if the hub reassigned/detached the chunk while the runner was down,
    **abandoned**: released with no epoch bump, so the runner never re-asserts authority
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
    """Park a paused chunk, else resume in place, else abandon it if the hub reassigned its chunk
    (issue #46), or if the hub no longer knows it at all (blizzard#9).

    The pause branch is **first**, and it keys on the pause *fact* rather than the derived
    status (issue #46). Both details are load-bearing:

    * **First**, because a paused chunk derives ``PAUSED``, not ``RUNNING`` — so before this
      branch existed a chunk still routed to *this* runner fell through to
      :func:`_abandon_reassigned`, giving up the claim, the route and every environment. A
      pause silently degraded into a detach on every restart, and RESUME runs before PULL, so
      PULL's own pause-park never got the chance to see it.
    * **The fact, not the status**, because ``status == PAUSED`` is a lossy read: PAUSED sits
      below the human-gated states in the derivation order, so a chunk both paused *and*
      parked on a question derives ``waiting_on_human``. A status-keyed check would never
      learn it was paused and would resume it on the answer.

    Conjoined with ``ours`` so a chunk that was **detached and then paused** still abandons:
    detach wins, because the route is gone and no amount of pausing makes it ours again. A chunk
    the hub has forgotten outright (a 404, :class:`ChunkNotFoundError`) abandons ahead of all
    three branches for the same reason (blizzard#9): there is no pause fact to read off a chunk
    that no longer exists."""
    try:
        detail = ctx.hub.get_chunk(lease.chunk_id)
    except ChunkNotFoundError:
        # The chunk is gone outright (e.g. a store reset) — terminal, not retryable; abandon now
        # rather than leave the intent open for PULL's `_reconcile_leases` to find it later.
        _abandon_reassigned(ctx, lease, via="resume")
        return
    except HubClientError:
        # Hub unreachable — the intent is durable and the environments stay held, so
        # leave it open and retry next tick. Resuming blind would risk re-asserting authority
        # over a chunk that may have been reassigned; the ownership check is worth the wait.
        return
    ours = detail.route is not None and detail.route.runner_id == ctx.config.runner_id
    if ours and detail.pause is not None:
        _kill_and_park_paused(ctx, lease, via="resume")
    elif detail.status == ChunkStatus.RUNNING and ours:
        _resume_in_place(ctx, lease)
    else:
        _abandon_reassigned(ctx, lease, via="resume")


def _resume_preamble(ctx: LoopContext, lease: LeaseRecord, bindings: list[EnvBindingRecord]) -> WorkerPreamble:
    """The per-lease identity a resumed worker needs to reach the runner for its lease.

    ``--resume`` inherits none of the spawn env, so a resumed worker's CLI
    (``blizzard runner attach``) and its heartbeat/SessionEnd hooks have no
    ``BLIZZARD_*`` identity unless it is re-supplied. The capability token's plaintext is
    never persisted (only its hash), so it is **re-minted** here — invalidating the prior
    one — and its hash re-recorded, exactly as :func:`_spawn_attempt` does at spawn. Every
    resume sibling (restart / answer / pause-lift) builds its resume env from this.
    """
    lease_token = secrets.token_urlsafe(_LEASE_TOKEN_BYTES)
    ctx.store.record_lease_token(lease.lease_id, hash_token(lease_token), ctx.clock.now())
    return WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id=b.environment_id, workdir=b.workdir) for b in bindings],
        lease_id=lease.lease_id,
        local_api_url=ctx.config.local_api_url,
        lease_token=lease_token,
    )


def _resume_in_place(ctx: LoopContext, lease: LeaseRecord) -> None:
    """Kill any survivor, then resume the session under the same lease/epoch/session.

    The fourth sibling of the resume family (spawn / judgement / answer): kill-first is what
    prevents two processes on one session — the epoch is not — and the session id, lease
    id, and epoch are all preserved, so the resumed worker's eventual completion carries the
    original epoch and the hub accepts it in place. Only ``pid``/``process_start_time`` are
    rewritten; no lease is minted and no closure is recorded, so no retry is consumed.

    A missing/corrupt session self-heals via the existing failure path: the resumed process
    cannot find its session, exits, and ADVANCE's verdict-less-exit failure requeues it fresh
     — no explicit detection needed here.

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
    away. It is bounded to that one call-return→store-write gap.

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
        ctx.process.kill(lease.pid)  # kill-first — never two processes on one session
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
    pid = ctx.harness.resume_with_message(
        bindings[0].workdir,
        lease.session_id,
        _RESTART_RESUME_MESSAGE,
        stdout_path=_stdout_path(ctx, lease.lease_id, _pending_generation(ctx, lease.lease_id)),
        preamble=_resume_preamble(ctx, lease, bindings),
        chunk_id=lease.chunk_id,
    )
    ctx.store.record_spawn(
        lease.lease_id,
        pid=pid,
        process_start_time=ctx.process.start_time(pid) or "",
        session_id=lease.session_id,  # unchanged — same session under the same lease
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
    """Release a chunk the hub reassigned, detached, or no longer knows about (blizzard#9) —
    reached from restart-resume or a live tick.

    No epoch bump and no requeue: the chunk is another runner's now (or detached to ``ready``, or
    gone outright — a 404 at the hub, e.g. after a store reset), so re-asserting authority over it
    would be wrong — the runner learns of it, whether over its own restart or on a live tick, and
    does exactly what losing ownership requires: kill the worker, release the environments. The lease is closed
    ``released`` (not a failed attempt — it never gets to run) and the intent is cleared. ``via``
    names which caller reached the ownership check that led here (``"resume"`` — restart-resume,
    ``"pull"`` — a live tick's :func:`_reconcile_leases`, ``"reap"`` — an escalation REAP
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


def _kill_and_park_paused(ctx: LoopContext, lease: LeaseRecord, *, via: str) -> None:
    """Kill a paused chunk's worker and park its lease — the claim is **kept** (issue #46).

    The deliberate inverse of :func:`_abandon_reassigned`, and a genuinely separate function
    rather than that one with a flag: the two diverge on every consequential point. This one
    does **not** release the environments (they stay held and warm for the resume), does not
    record a closure (the lease stays ACTIVE), does not bump the epoch, mints no lease, and
    records no requeue — so **no retry is consumed** and the route, epoch and session all
    survive the pause. What ends is the *process*, not the tenure. Detach gives the work away;
    a pause holds it exactly where it is.

    ``via`` names which caller reached the pause check that led here (``"resume"`` — a chunk
    paused while the runner was down, ``"pull"`` — paused while it was up), following the
    module's twin-caller convention.

    **Not gated by :func:`_spawn_suppressed`.** A kill is not a spawn, and a chunk pause is a
    hub-level instruction over one specific chunk — orthogonal to the runner's own brake, so
    one must not suppress the other. This is the same reason the abandon kill that
    :func:`_reconcile_leases` reaches (:func:`_abandon_reassigned`) is ungated. It reads as an
    asymmetry against REAP's stall kill, which *is*
    deferred while locally paused, so the distinction is worth naming: there the local brake is
    the **only** authority saying anything about that worker, and killing it would make a pause
    into a drain. Here a second authority — the hub, about this chunk — has said stop, and
    honoring it is not the brake's business either way.

    ``record_resume_clear`` unconditionally is correct and inert when there is no mark:
    ``_intent_is_open`` is timestamp-correlated, so clearing an unmarked lease writes a row no
    predicate reads (exactly as :func:`_abandon_reassigned` already does). It matters on the
    RESUME path, where a marked lease must not be left holding an open intent ADVANCE would
    then skip on forever.

    Crash window (``bzh:crash-point-registry``): :data:`_CP_PAUSE_PARK_AFTER_KILL` sits between
    the kill and the durable park — see its declaration for why recovery converges."""
    now = ctx.clock.now()
    if lease.pid is not None:
        ctx.process.kill(lease.pid)
    _CP_PAUSE_PARK_AFTER_KILL.reached()  # worker dead; the park is not yet durable
    ctx.store.record_pause_park(lease_id=lease.lease_id, chunk_id=lease.chunk_id, parked_at=now)
    ctx.store.record_resume_clear(lease_id=lease.lease_id, cleared_at=now)
    _log.info(
        "parked chunk on an operator pause — claim retained",
        chunk_id=lease.chunk_id,
        lease_id=lease.lease_id,
        epoch=lease.epoch,
        via=via,
    )


# --------------------------------------------------------------------------- #
# PULL
# --------------------------------------------------------------------------- #


def pull(ctx: LoopContext) -> None:
    """Exchange facts with the hub (outbound-only): sync the registry, learn of any
    detach/reassignment, drain the buffer.

    Three outbound exchanges happen here. First :func:`_sync_registry` registers the runner
    (idempotent — refreshing its ``last_seen_at`` liveness) and reads its declarative
    pause brake back, mirroring it locally so FILL adheres. Then :func:`_reconcile_leases`
    asks the hub, per active lease, whether this runner still holds the route — the same
    ownership question restart-resume already asks — and abandons any lease it no longer holds,
    or parks one the operator paused (issue #46), before anything is flushed. Then the
    outbound buffer drains.

    Store-and-forward always: every hub-bound fact was written to the buffer at mint
    with a per-runner monotonic seq, and this is the single flusher that drains it — FIFO, so
    a ``lease.minted`` always precedes the completion minted under it. A completion's flush is
    special: its apply-response carries the chunk's next node envelope, so the flusher
    drives the continue-in-place here. A transport failure stops the drain (the buffer is the
    only ordered path — a later fact must not overtake a stuck earlier one) and the backlog
    flushes next tick; an outage is just a bigger backlog.
    """
    _sync_registry(ctx)
    _reconcile_leases(ctx)
    _CP_PULL_BEFORE.reached()
    flush_outbound(ctx)
    _CP_PULL_AFTER.reached()


def _sync_registry(ctx: LoopContext) -> None:
    """Register + heartbeat and mirror the hub's pause brake locally.

    Registration is idempotent and doubles as the runner-level liveness heartbeat — a
    per-pull refresh of ``last_seen_at``, much slower than the machine-local worker
    heartbeat. The declarative pause brake is then read back and mirrored to the
    runner store so FILL adheres without a hub call; when the hub is unreachable the last
    mirrored value holds, so the runner keeps obeying its last-known directive.
    """
    try:
        ctx.hub.register_runner(ctx.config.runner_id, ctx.config.workspace_id, env_capacity=ctx.config.env_capacity)
        paused = ctx.hub.fetch_runner_paused(ctx.config.runner_id)
    except HubClientError:
        return  # hub unreachable — keep the last-mirrored brake
    ctx.store.set_hub_paused(ctx.config.runner_id, paused=paused, at=ctx.clock.now())


def _reconcile_leases(ctx: LoopContext) -> None:
    """Reconcile every active lease against the hub's view of its chunk — abandon it if the hub
    no longer routes it here, else park it if the operator paused it (issue #46).

    A live tick's half of restart-resume's ownership check (:func:`_resume_marked_lease`), and
    the two questions share **one** ``get_chunk`` per lease: this sweep already made that call
    for the detach check, and the pause answer rides the very same response, so honoring a pause
    on a live tick costs no extra hub polling at all.

    For every active lease, ask the hub who holds the chunk's route now. Unreachable hub →
    ``continue``: keep working, the last-known directive holds (the same rule
    :func:`_sync_registry` follows) — do not crash, do not abandon on a transport failure, and do
    not read a transport failure as a pause either. Then, in order:

    * **Unknown at the hub** (a 404, :class:`ChunkNotFoundError`) → :func:`_abandon_reassigned`:
      terminal, not retryable (blizzard#9) — the chunk's tenure ended out from under this
      runner, so the worker is reaped and the environments released rather than the read retried
      forever. Caught **ahead of** the ``HubClientError`` arm below, which it subclasses.
    * **Stopped** (``detail.status is ChunkStatus.STOPPED``) → :func:`_abandon_reassigned`
      (issue #118): checked **first** of the fact/status branches, ahead of the route check
      below, so this runner honors the terminal fact directly rather than depending on
      ``stop``'s own route release having landed — a belt-and-suspenders backstop, not a
      replacement, for the ordinary case where the route is already gone by the time this
      sweep asks.
    * **Detached or reassigned** (``route is None`` or someone else's ``runner_id``) →
      :func:`_abandon_reassigned`: kill the worker, release every environment, close the lease
      ``released`` with no epoch bump, no requeue fact, no retry consumed. Checked **first** of
      the two fact branches, so a chunk that was detached *and* paused abandons — detach wins,
      the route is gone.
    * **Paused** (``detail.pause`` is set) → :func:`_kill_and_park_paused`: kill the worker but
      keep the claim, the route, the epoch and the environments.
    * Otherwise → leave it alone, whatever its derived status: a live runner legitimately holds
      an active lease while the chunk derives ``delivering``, ``waiting_on_human``, or
      ``needs_human`` (a hub-node hold or an open escalation), so — unlike the restart-resume
      predicate, which also checks ``status == RUNNING`` because at restart a non-running status
      means the world moved on — the check here is route identity **alone**.

    The pause branch keys on the ``pause`` **fact**, not the derived status, and that is what
    makes the overlap with an ask-park work: a paused chunk that is also parked on a question
    derives ``waiting_on_human``, so a status-keyed check would miss the pause entirely. Parking
    an already-ask-parked lease is safe — the kill is a no-op on an already-dead worker and the
    pause-park is additive to the ask-park, which stays open underneath and is delivered by
    :func:`_resume_if_answered` on the tick after the pause clears.

    The park is guarded by :meth:`pause_parked_lease_ids` (read **once**, hoisted out of the
    loop) so it is idempotent across ticks: without the guard every tick of a standing pause
    would append another park row for the same lease — unbounded growth, and an
    ``open_pause_park`` whose answer depends on which duplicate it read. The
    ``runner:one-open-pause-park-per-lease`` invariant (``bzh:invariant-checker``) fences
    exactly this.

    Runs before the flush, deliberately: killing the detached chunk's worker as early **within
    this step** as possible is the best lever the runner has on the late-write window — between
    the detach and the chunk's re-claim by some runner, this runner's already-buffered facts for
    the chunk can still flush and be accepted; only a new lease's floor closes that.
    It is not the earliest point in the *tick* — REAP and RESUME both precede PULL, and REAP's own
    failed-attempt path (:func:`_fail_attempt`) makes the same ownership check before escalating,
    so a detach discovered there is abandoned on the spot rather than left for this pass to find.
    Killing the worker before the flush narrows the window but cannot purge the buffer:
    ``bzh:invariant-checker`` requires a gapless outbound-buffer sequence, so deleting buffered
    facts to close it would trade a durable invariant for a window the fence closes anyway. This is
    requeue's existing window (requeue already releases the route with no bump too) — not engineered
    around here."""
    pause_parked = ctx.store.pause_parked_lease_ids()  # hoisted: the park guard, one read per tick
    for lease in ctx.store.list_active_leases():
        try:
            detail = ctx.hub.get_chunk(lease.chunk_id)
        except ChunkNotFoundError:
            # The chunk is gone from the hub outright — terminal, not retryable (blizzard#9).
            # Ordered before the HubClientError arm because it is a *subclass* of it: without
            # this arm the 404 would be swallowed as "hub unreachable" and this sweep would
            # re-ask forever, never reaping the worker or releasing the environments.
            _abandon_reassigned(ctx, lease, via="pull")
            continue
        except HubClientError:
            continue  # hub unreachable — last-known directive holds; keep working
        if detail.status == ChunkStatus.STOPPED:
            # Honor the terminal fact directly (issue #118) — do not wait on the route
            # check below to observe the release; see the docstring's ordering note.
            _abandon_reassigned(ctx, lease, via="pull")
        elif detail.route is None or detail.route.runner_id != ctx.config.runner_id:
            _abandon_reassigned(ctx, lease, via="pull")
        elif detail.pause is not None and lease.lease_id not in pause_parked:
            _kill_and_park_paused(ctx, lease, via="pull")


def _reassigned_or_detached(ctx: LoopContext, lease: LeaseRecord) -> bool:
    """True iff the hub no longer routes ``lease``'s chunk to this runner, or the
    chunk is gone outright (blizzard#9).

    Unreachable hub → ``False``: last-known directive holds — a transport failure is
    never read as a detach. A 404 (:class:`ChunkNotFoundError`) is the one exception to that
    rule: the hub telling us the chunk no longer exists (e.g. a store reset) is not a transport
    failure to wait out — it is terminal, so this reads it as detached too and lets the caller's
    abandon path reap the lease and release the held environments rather than retry the 404
    forever (blizzard#9).

    :func:`_fail_attempt`'s escalate guard is this function's caller: a single lease, checked
    only on the exhausted-retries path. PULL's own sweep (:func:`_reconcile_leases`) once shared
    it, but now inlines the ``get_chunk`` so its detach and pause branches can read one response
    instead of polling the hub twice — it carries its own copy of the 404 rule above, for the
    same reason and to the same effect. This stays a function because the two ask the same
    ownership question at very different rates, and the escalate guard needs the answer where no
    sweep is running."""
    try:
        detail = ctx.hub.get_chunk(lease.chunk_id)
    except ChunkNotFoundError:
        return True  # the chunk no longer exists at the hub — terminal, not retryable
    except HubClientError:
        return False  # hub unreachable — last-known directive holds; keep working
    return detail.route is None or detail.route.runner_id != ctx.config.runner_id


def flush_outbound(ctx: LoopContext) -> None:
    """Drain the outbound buffer in FIFO order until a fact fails to deliver."""
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
    """Push a ``lease.minted`` / ``escalation.recorded`` fact to POST /events."""
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
    """Submit a buffered completion and drive its apply-response.

    Idempotent by construction: the hub's completion apply is epoch-idempotent (a
    re-applied completion returns its original outcome without a second transition),
    and the runner acts on the response only while the lease is still active —
    a re-flush after a lost ack finds the lease closed and simply clears the buffer.
    """
    payload = json.loads(fact.payload)
    submission = CompletionSubmission.model_validate(payload["submission"])
    _CP_FLUSH_BEFORE_SUBMIT.reached()
    try:
        response = ctx.hub.submit_completion(fact.chunk_id or "", submission)
    except HubClientError:
        return False  # completion stays durable in the buffer; the mid-node worker is unaffected

    _CP_FLUSH_AFTER_SUBMIT.reached()  # hub applied it; a crash here is the lost-ack replay
    ctx.store.ack_outbound(fact.seq, acked_at=ctx.clock.now())
    _CP_FLUSH_AFTER_ACK.reached()
    lease = ctx.store.active_lease(fact.lease_id or "")
    if lease is None:
        # Already advanced on an earlier flush whose ack was lost — nothing to do.
        return True
    _consume_apply_response(ctx, lease, response)
    _CP_FLUSH_AFTER_APPLY.reached()
    return True


def _flush_decision(ctx: LoopContext, fact: BufferedFact) -> bool:
    """Submit a buffered runner-config gate decision and park the chunk.

    A gated node's decision parks the chunk ``waiting_on_human`` — there is no next
    envelope to continue into, so the flush just closes the lease (the node-step is
    done) and holds the environments. Idempotent by construction: the hub's decision
    apply is natural-key idempotent (a re-submitted decision at the same (node, epoch)
    returns the parked outcome without a second row), and a re-flush past a lost
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
        return True  # already parked on an earlier flush whose ack was lost
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
    """Record the closure and continue in place per the hub's apply-response.

    Between the closure below and any next-attempt spawn is the between-attempts
    step boundary the per-chunk spend cap checks at (issue #61a, :func:`_park_on_cost_cap`):
    the attempt just closed is genuinely done — its worker already exited, its completion
    already applied at the hub — so parking it here kills nothing live. This is deliberately
    not inside :func:`_spawn_attempt`, whose silent-``None`` return (issue #45) forbids a
    diverting escalation.
    """
    if response.outcome == ApplyOutcome.FAILURE:
        # A semantic rejection — a stale-epoch (zombie) or terminal completion. The
        # attempt failed; requeue or escalate. The chunk never advanced and never
        # entered the merge queue (the hub fenced it before any write).
        _log.warning("completion rejected on flush", chunk_id=lease.chunk_id, detail=response.detail or "")
        _fail_attempt(ctx, lease, reason=_FAILED, via="pull")
        return
    now = ctx.clock.now()
    ctx.store.record_closure(
        lease_id=lease.lease_id, chunk_id=lease.chunk_id, node_id=lease.node_id, reason=_TRANSITIONED, closed_at=now
    )
    _CP_ADV_AFTER_CLOSURE.reached()
    if response.outcome == ApplyOutcome.NEXT and _park_on_cost_cap(ctx, lease):
        return  # capped — needs_human; the next attempt is not spawned
    bindings = ctx.store.bindings_for_chunk(lease.chunk_id)
    _apply_response(ctx, lease.chunk_id, response.outcome, response.next_envelope, bindings)


def _park_on_cost_cap(ctx: LoopContext, lease: LeaseRecord) -> bool:
    """True — chunk parked ``needs_human`` — iff its spend has reached ``cost.chunk_cap_usd``.

    Reads the hub-**derived** total (``ChunkDetail.cost``, ``bzh:facts-not-status``): usage
    is a fact and a chunk's cost is a read-time aggregate over it, never a stored column, so
    this is the single source of truth for "how much has this chunk spent" and the runner
    never sums usage locally to answer that question.

    **Cost-absent conservative treatment (epic #57 phase 5, resolved product decision):** a
    usage row with no ``cost_usd`` (the harness-envelope-less transcript-token fallback, e.g.
    after a reaped crash) contributes its tokens to the total but **$0** to the cost sum — so
    the summed ``cost_usd`` is a LOWER BOUND, never an over-estimate, and ``cost_partial`` is
    set whenever any such row fed it. The cap trips on this lower bound (a capped chunk's true
    spend may be higher still); the escalation log line below states PARTIAL whenever that is
    so, so an operator reading the takeover is never told a partial total is the whole spend.

    ``cost.chunk_cap_usd`` absent means no cap — unchanged pre-#61a behavior. A transport
    failure reading the chunk detail defers the check to the next boundary (the same
    "last-known-directive holds" rule every other hub-unreachable branch in this module
    follows) rather than blocking the chunk's advance on a flaky poll.

    Reuses :func:`_escalate` — the same ``escalation.recorded`` fact, pasteable takeover
    command, and held environments as a retries-exhausted escalation — over ``lease``, whose
    closure this function's caller already recorded as ``transitioned`` (the attempt genuinely
    succeeded; this is not a failure and consumes no retry).
    """
    cap = ctx.config.chunk_cap_usd
    if cap is None:
        return False
    try:
        detail = ctx.hub.get_chunk(lease.chunk_id)
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
    _escalate(ctx, lease, reason=f"spend cap ${cap:.2f} reached (spend ${cost.cost_usd:.2f}{partial_note})")
    return True


# --------------------------------------------------------------------------- #
# FILL
# --------------------------------------------------------------------------- #


def fill(ctx: LoopContext) -> None:
    """Keep the fleet busy: peek → acquire → claim-by-route → bind → spawn.

    FILL is where work is claimed. Open agent slots are
    ``MAX_AGENTS - active_leases``; for each, peek the ready queue, acquire the
    chunk's environments (all-or-nothing), and POST the complete route. A 409 is
    race-second-place — release the bindings and move on. A 403 (issue #44) is a
    different shape: the hub's registry already has this runner paused and refused
    the claim outright, closing the gap between a hub pause landing and this
    runner's next pull mirroring it — release the binding and stop filling this tick
    rather than keep racing claims the hub will refuse the same way. The winning
    claim carries the first node envelope, so the worker starts without a second
    round-trip.

    The pause brake has two independent surfaces and FILL claims nothing while
    **either** is set: the hub's flag (mirrored locally by PULL) and this runner's own
    local flag (``PATCH /runner``, issue #43), which the operator sets machine-locally and
    which therefore holds with the hub unreachable. In-flight chunks are untouched under
    either — FILL only ever stops *new* claims — but since issue #45 the two brakes'
    reach beyond FILL diverges: the hub brake keeps its claims-only meaning (checked
    here alone), while the local brake also blocks every other spawn site (restart-resume,
    an answer-resume, ADVANCE's next-node, a requeue or claim-adopt respawn, and ADVANCE's
    judgement resume) via :func:`_spawn_suppressed`, its one shared home, and defers
    escalation (:func:`_fail_attempt`'s exhausted-budget branch) the same way REAP's own
    kill of a stalled worker is deferred — a locally-paused runner starts no process and
    hands nothing off as unrecoverable while it waits. So a hub-only pause still drains the
    fleet the way it always has; a local pause spawns nothing, anywhere, while
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
    """Reconcile bindings left by a crash in FILL's bind→claim→spawn window.

    Because the binding is written locally *before* the hub claim, a crash anywhere in
    that window leaves the runner holding a binding for a chunk with no active lease.
    This runs before FILL peeks new work and, per the hub's view of each such chunk —

      * route ours, still ``running`` → **adopt**: spawn the current node into the warm
        environment, finishing the interrupted claim (the lease never minted);
      * no live route (``ready``), or a route held by another runner → **release** the
        orphaned binding (the claim never landed, or we lost the race before retracting
        it) so the environment frees this tick and the chunk re-derives ``ready``.

    A chunk at a hub node (``delivering``) keeps its binding and is left to ADVANCE — only
    a chunk the runner should be actively working, but isn't, is reconciled here. A chunk
    awaiting a human is likewise left to ADVANCE **unless** a local requeue mark clears
    it first (issue #53, below) — that mark is exactly what tells "awaiting a human" from
    "the human is done, spawn it": a requeued chunk is no longer awaiting anyone.

    A 404 (:class:`ChunkNotFoundError`) is a third, terminal shape, same as
    :func:`_advance_held_chunk` (blizzard#9): the hub no longer knows this chunk, so the
    orphaned binding is released rather than left for this reconciler to keep re-asking
    about forever — the generic :class:`HubClientError` branch below is for a transport
    failure alone, not this one."""
    requeue_pending = ctx.store.pending_requeue_chunk_ids()  # hoisted: one read per FILL, not per chunk
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
        if chunk_id in requeue_pending:
            # The human cleared this chunk's local hold (``blizzard runner requeue``,
            # issue #53) — spawn its fresh attempt ahead of every other branch below, the
            # same priority the gate/hub-node guard gets: no other case in this function
            # should second-guess an explicit human decision.
            ours = detail.route is not None and detail.route.runner_id == ctx.config.runner_id
            if not ours:
                _log.info("releasing binding — chunk requeued locally but no longer routed here", chunk_id=chunk_id)
                _release_all(ctx, chunk_id)
                continue
            _resume_requeued_chunk(ctx, chunk_id)
            continue
        if detail.decision is not None:
            # A chunk carrying a live gate decision — open (``waiting_on_human``) or
            # resolved-but-not-transitioned — is owned by ADVANCE's :func:`_advance_held_chunk`,
            # which records the resolving transition. A *resolved* gate keeps its route
            # live so it derives ``running`` with no active lease — the same shape as an
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
        # Not capacity — a reset-on-acquire step failed. Surface it as an
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

    # Record the chunk→env binding locally BEFORE claiming at the hub: the binding
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
        # Ambiguous: the request may never have reached the hub, or the hub may have
        # committed the claim (issue #84b's ``claim.after-persist.before-response``
        # crash point — persisted, then the process died before the response landed)
        # and this runner simply never read the outcome back. Releasing the binding
        # here unconditionally would be *wrong* in the second case: the hub would show
        # a live route this runner holds while the runner has already freed the
        # environment for other work, permanently stranding the chunk. Leave the
        # binding exactly as :func:`_reconcile_interrupted_claims` already handles a
        # runner-side crash in this same window — its next tick resolves the ambiguity
        # for real, off the hub's own authoritative answer: adopt if the claim landed,
        # reclaim (fresh) if it did not.
        return False
    if outcome.denied_paused is not None:
        # The hub's registry already has us paused — a distinct outcome from losing
        # the exactly-once race (issue #44): this claim was refused outright, not
        # beaten. Stop filling this tick rather than burn the remaining slots on
        # claims the hub will refuse the same way; PULL mirrors the flag locally on
        # its next pull.
        _log.info(
            "route claim denied — runner paused at the hub", chunk_id=entry.chunk_id, runner_id=ctx.config.runner_id
        )
        _release_binding(ctx, entry.chunk_id, acquired)
        return False
    if outcome.denied_terminal is not None:
        # The chunk was stopped (or otherwise reached done) between this peek and this
        # claim POST (issue #118) — not a race loss, the chunk itself is why. The ready
        # queue's own peek-time filter cannot see this: it only excludes a chunk that
        # already derived non-ready when it was peeked. Undo the binding and move on;
        # this chunk cannot reappear at the ready queue's head to be peeked again.
        _log.info(
            "route claim denied — chunk is terminal",
            chunk_id=entry.chunk_id,
            status=outcome.denied_terminal.status,
        )
        _release_binding(ctx, entry.chunk_id, acquired)
        return True  # peek fresh next iteration
    if outcome.conflict is not None or outcome.claimed is None:
        _log.info("route claim lost the race", chunk_id=entry.chunk_id)
        _release_binding(ctx, entry.chunk_id, acquired)  # someone else won — undo our binding
        return True  # peek fresh next iteration

    _CP_FILL_AFTER_CLAIM.reached()
    # Stash the won claim's plaintext route token (issue #84a) before spawning: the
    # first thing a chunk-scoped fact enqueues under this route reads it back out of
    # the store, never off `outcome.claimed` directly — the same store round-trip the
    # reclaim path below shares, and requeue/takeover/retries later re-read the same
    # row rather than re-claiming.
    ctx.store.set_route_token(entry.chunk_id, token=outcome.claimed.route_token, at=ctx.clock.now())
    _spawn_attempt(ctx, entry.chunk_id, outcome.claimed.envelope, acquired, via="fill")
    return True


# --------------------------------------------------------------------------- #
# ADVANCE
# --------------------------------------------------------------------------- #


def advance(ctx: LoopContext) -> None:
    """Judge finished workers and move chunks through the graph.

    Two responsibilities: (a) a session-bearing worker whose process has exited is a
    done declaration — resume it with the judgement prompt, parse the ``<Choice>``,
    push its artifacts, and **buffer** the epoch-fenced completion (the flusher in
    PULL delivers it and drives the apply-response) — unless this operator gates
    the node by name, in which case it buffers a **decision** instead; (b) a
    chunk the runner holds with no active lease is driven by :func:`_advance_held_chunk`
    — a hub node polled for its terminal outcome, or a gate whose decision the
    human has resolved advanced by the resolving transition.

    A worker whose completion or decision is already buffered is skipped: the outcome is
    elicited exactly once, then the chunk waits at its node boundary for the flush.

    A dormant lease routes to whichever of the two resume siblings its park calls for, and
    **pause dominates the ask** (issue #46). The overlap is real, not hypothetical: an operator
    may pause a chunk that is already ``waiting_on_human`` (pause is deliberately not refused
    there), and PULL keys on the pause *fact* rather than the derived status, so it happily
    pause-parks a lease that is already ask-parked. Ordering the pause branch first — together
    with :func:`_resume_if_unpaused`'s own ask-park early return — is what makes **an answer
    not un-pause a chunk**: the pause-park clears when the operator resumes, and the *next*
    tick's :func:`_resume_if_answered` delivers the answer. Ordered the other way,
    :func:`_resume_if_answered` would resume a paused worker and PULL would kill it again the
    next tick — a spawn/kill churn loop.

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
    brake forbids), and a worker killed mid-work is not a done declaration even
    though its process is gone.

    **A chunk under an open takeover (issue #52) is skipped in both loops below**: the
    human holds the session, so neither the judgement/resume elicitation nor the
    held-chunk gate/hub-node poll may touch it until the takeover ends.
    """
    pending = ctx.store.pending_submission_lease_ids()
    ask_parked = ctx.store.ask_parked_lease_ids()
    pause_parked = ctx.store.pause_parked_lease_ids()
    resume_intents = ctx.store.resume_intent_lease_ids()
    taken_over = ctx.store.open_takeover_chunk_ids()
    for lease in ctx.store.list_active_leases():
        if lease.chunk_id in taken_over:
            continue  # the human holds this session — no loop step touches it
        if lease.pid is None or lease.session_id is None:
            continue  # REAP's residue
        if lease.lease_id in resume_intents:
            continue  # RESUME hasn't re-attached (or abandoned) it yet — not exited work
        if lease.lease_id in pending:
            continue  # outcome elicited, awaiting flush — the node boundary
        if lease.lease_id in pause_parked:
            _resume_if_unpaused(ctx, lease)  # dormant on an operator pause — resume when it lifts
            continue
        if lease.lease_id in ask_parked:
            _resume_if_answered(ctx, lease)  # dormant on a question — resume on the answer
            continue
        if ctx.process.is_alive(lease.pid, lease.process_start_time or ""):
            continue  # worker still running
        _advance_exited_worker(ctx, lease)

    for chunk_id in ctx.store.live_tenure_chunk_ids():
        if chunk_id in taken_over:
            continue  # the human holds this chunk — no gate/hub-node poll while they do
        if ctx.store.active_lease_for_chunk(chunk_id) is None:
            _advance_held_chunk(ctx, chunk_id)


def _advance_exited_worker(ctx: LoopContext, lease: LeaseRecord) -> None:
    """Park on an open ask, else elicit the verdict and buffer the completion.

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

    # Ask-and-exit: a worker that exited holding an unforwarded ask
    # parked on a question — forward it and park, no verdict, no retry consumed. This is
    # what tells a park from a failure: an exit with an open ask is a park; an exit with
    # neither is a failure. The park fact stops REAP's clock and makes the chunk derive waiting_on_human.
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

    # 1. Push produced branches to their forge origins BEFORE submitting. Not a
    #    harness spawn, and idempotent, so this runs regardless of the local brake — the
    #    branch is forge state the runner already holds, not new work being started.
    _CP_ADV_BEFORE_PUSH.reached()
    artifacts = _push_and_collect_artifacts(ctx, bindings)
    _CP_ADV_AFTER_PUSH.reached()

    # 1b. Runner-config gate: this operator gates this node by name, so the
    #     node-step's outcome is a human's, not the worker's. Submit a Decision carrying
    #     the step's artifacts instead of eliciting a verdict — the human judges.
    #     Not a spawn either — parking for a human is not starting a process — so this
    #     also proceeds regardless of the local brake.
    if lease.node_name in ctx.config.gates:
        _buffer_decision(ctx, lease, artifacts)
        return

    # 2. Elicit the verdict via the judgement resume — the fourth spawn primitive
    #    (issue #45), gated here rather than hoisted to the top of this function so the
    #    park/gate/push work above (none of it a spawn) still happens while paused.
    if _spawn_suppressed(ctx, via="advance", chunk_id=lease.chunk_id, lease_id=lease.lease_id):
        return

    # A dead worker whose session cannot answer a parseable <Choice> is a failure.
    prompt = (envelope.judgement_prompt or "") + _elicitation_tail(envelope)
    # The adapter works in a directory; the runner resolves the provider-returned
    # workdir from the binding and supplies it.
    output = ctx.harness.judge(bindings[0].workdir, lease.session_id, prompt)

    # 2c. Record this attempt's harness usage (issue #58) — the spawn/resume invocation
    #     that just exited and the judgement resume above, each its own fact. Recorded
    #     *before* the verdict is parsed so it lands on the verdict-less-fail exit too:
    #     that attempt burned the same spawn + judge invocations, so failing it into a
    #     fresh retry (which mints a new lease and discards this one's stdout) must not
    #     also discard its spend. Idempotent on ``(lease, generation, kind)``, so the
    #     success path re-running it below is harmless.
    _record_attempt_usage(ctx, lease, bindings, judge_output=output)

    choice = ctx.harness.parse_verdict(output)
    if choice is None:
        _log.warning("verdict-less judgement — failing attempt", chunk_id=lease.chunk_id, lease_id=lease.lease_id)
        _fail_attempt(ctx, lease, reason=_FAILED, via="advance")
        return
    _CP_ADV_AFTER_JUDGE.reached()
    _CP_ADV_AFTER_USAGE.reached()

    # 2a. Nudge-once (issue #113, Phase 4): a `produces` name this attempt covers
    #     with neither a pushed git commit nor an explicit attachment gets exactly one
    #     resumed nudge, gated on a durable fact keyed `(lease, epoch)` so a later
    #     ADVANCE re-drive of this same attempt (a retried judgement poll, a crash
    #     recovery) never repeats it (`bzh:invariant-checker` —
    #     "at most one nudge per (lease, epoch)"). The resume is a spawn primitive, but
    #     needs no separate `_spawn_suppressed` check of its own: this function already
    #     gated its one entry into spawn territory above (comment 2), and a suppressed
    #     tick never reaches this line at all.
    #
    #     The fact is recorded BEFORE the resume runs, not after. Every other
    #     resume-then-record pairing in this module (`_resume_if_answered`,
    #     `_resume_if_unpaused`) records after because the fact it writes carries the
    #     resume's own output (a new pid) — it cannot exist sooner. This fact carries
    #     no such output: it is a pure guard, so nothing blocks writing it first, and
    #     writing it first is what makes "at most one nudge" a structural guarantee
    #     rather than a hope. A kill -9 anywhere from this write onward can never lead
    #     to a second resume attempt for this attempt, because the next ADVANCE pass
    #     consults the fact alone, never the resume's outcome. The alternative
    #     ordering (record after) leaves a window — a crash between the resume
    #     returning and the fact landing — where recovery cannot tell "nudged, worker
    #     ignored it" from "never nudged" without trusting the worker's compliance,
    #     which a crash-correctness guarantee cannot rest on. A crash before this
    #     write (there is nothing to arm — the write is the first mutation in this
    #     branch) simply leaves the fact unset, so the very next pass evaluates the
    #     same missing-set fresh and decides again, same as if this branch had never
    #     started.
    assessment = ctx.harness.parse_assessment(output)
    attachments = ctx.store.attachments_for_lease(lease.lease_id)
    missing = _missing_produces(envelope, artifacts, attachments)
    if missing and not ctx.store.nudge_fired(lease.lease_id, lease.epoch):
        _log.warning(
            "nudging worker for unattached produces names",
            node=envelope.node.node_name,
            missing=missing,
            lease_id=lease.lease_id,
            epoch=lease.epoch,
        )
        ctx.store.record_nudge_fired(lease_id=lease.lease_id, epoch=lease.epoch, at=ctx.clock.now())
        _CP_NUDGE_AFTER_FIRED_FACT.reached()
        # `judge`, not `resume_with_message`, on purpose: this call's own reply is
        # discarded (the nudge elicits no verdict of its own — the original judgement
        # above already stands), but the resume must still be *synchronous* — the
        # `attachments_for_lease` re-read just below has to observe whatever the worker
        # attached while the nudge ran, and only `judge`'s synchronous session-resume
        # guarantees the worker has already replied (and so had the chance to attach)
        # before this function reads on. `resume_with_message` only returns a new pid
        # (issue #113, Phase 4) — it would race the re-read against a worker still
        # composing its attach.
        nudge_output = ctx.harness.judge(bindings[0].workdir, lease.session_id, _nudge_message(missing))
        _CP_NUDGE_AFTER_RESUME.reached()
        # Record this invocation's own usage (issue #58) — a distinct `nudge` kind so it
        # cannot collide with (or be mistaken for) the primary judgement's own `judge`
        # fact already recorded above at this same generation (`_record_attempt_usage`);
        # the generation itself does not advance for this resume (no `record_spawn`
        # call — the pid is unchanged), so it is read fresh here rather than threaded
        # through from `_record_attempt_usage`, but resolves to the same value.
        nudge_generation = ctx.store.lease_generation(lease.lease_id)
        nudge_sample = ctx.harness.parse_usage(nudge_output, "nudge")
        if nudge_sample is not None:
            _store_usage(ctx, lease, generation=nudge_generation, sample=nudge_sample)
        # Re-read: a worker that attached during the nudge must have its content picked
        # up before assembly below, not the assessment fallback it just corrected.
        attachments = ctx.store.attachments_for_lease(lease.lease_id)

    # 2b. Harvest the node's asset artifacts: a node that `produces` a name no
    #     pushed git commit covers (the review node's `findings`) emits an explicit
    #     `blizzard runner attach --name` submission for that name where one exists
    #     (issue #113), read from the durable store so a restart between attach and
    #     completion still sees it, else falls back to the worker's assessment as
    #     before — either way carried back into the build envelope latest-by-epoch on
    #     a fail judgement.
    artifacts += _collect_asset_artifacts(envelope, artifacts, assessment, attachments)

    # 3. Buffer the completion — one atomic, epoch-fenced write, delivered by
    #    the flusher. The buffer entry names the lease so the flush drives its
    #    apply-response; ADVANCE will skip this lease until the flush closes it.
    submission = CompletionSubmission(
        choice=choice,
        epoch=lease.epoch,
        runner_id=ctx.config.runner_id,
        from_node_id=lease.node_id,
        check_results=[],  # in-session check assessment is P7; the model carries them
        artifacts=artifacts,
        route_token=ctx.store.route_token(lease.chunk_id),  # issue #84a — stamped at enqueue
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
    """Buffer a runner-config gate decision — the gated node-step's outcome.

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
        route_token=ctx.store.route_token(lease.chunk_id),  # issue #84a — stamped at enqueue
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
    """Act on the apply-response: continue in place, hold at a hub node, or finish."""
    if outcome == ApplyOutcome.NEXT and next_envelope is not None:
        envs = _bindings_as_environments(bindings)
        _spawn_attempt(ctx, chunk_id, next_envelope, envs, via="apply-response")
    elif outcome == ApplyOutcome.HUB_NODE_TAKEN:
        _log.info("hub node took over — holding envs until terminal", chunk_id=chunk_id)
    elif outcome == ApplyOutcome.MIGRATED:
        # A cross-graph migration re-pinned + re-queued the chunk hub-side (#90) and
        # already released its route — tear the attempt down and do NOT continue in
        # place; the chunk is claimed afresh under the new graph. Like DONE (release
        # envs), but the chunk re-queues rather than finalizing.
        _log.info("chunk migrated to another graph — releasing envs", chunk_id=chunk_id)
        _release_all(ctx, chunk_id)
    elif outcome == ApplyOutcome.DONE:
        _release_all(ctx, chunk_id)
    elif outcome == ApplyOutcome.PARKED_AT_GATE:
        _log.info("chunk parked at human gate", chunk_id=chunk_id)  # waiting_on_human


def _advance_held_chunk(ctx: LoopContext, chunk_id: str) -> None:
    """Drive a chunk the runner holds with no active lease: a hub node, a parked
    gate, or a chunk the hub has just routed into a fresh runner node.

    Three parked shapes share this poll (all hold environments, no live lease): a
    chunk at a **hub node** (a generic hub command node, #65) is polled for its
    terminal outcome and released once it reaches `done`; a chunk **parked on a
    resolved gate decision** is advanced by recording the resolving transition along
    the chosen edge, then continued in place from the returned envelope — the human's
    choice moves the chunk; a chunk the hub has advanced to a **higher epoch** than
    this runner has minted a lease for — its newest transition now targets a plain
    **runner node** under the executor's own ``hub_epoch``, an authored
    ``merged -> <node>`` edge (#63) landing the chunk into a post-merge node, or a conflict
    routed back to a worker node — is advanced by :func:`_spawn_into_held_node`: fetch the
    fresh envelope and spawn it into the already-held, warm environments (the same
    :func:`_spawn_attempt` path :func:`_apply_response`'s ``NEXT`` branch uses). This is
    the "runner advances the chunk into `<node>`" mechanism #63 names; it also subsumes the
    conflict-reappears case once deferred here. The **strictly-higher hub epoch** is what
    distinguishes a genuine hub advance from a chunk whose just-recorded escalation is still
    buffered (the hub reads ``running`` for a beat, at the epoch this runner already holds) —
    spawning on ``running`` alone would re-spawn the escalated node in an endless loop.

    A 404 (:class:`ChunkNotFoundError`) is a fourth, terminal shape (blizzard#9): the hub no
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
        return
    hub_epoch = detail.latest_epoch
    if detail.status == ChunkStatus.RUNNING and hub_epoch is not None and hub_epoch > ctx.store.latest_epoch(chunk_id):
        # The hub has advanced this chunk to a **higher epoch** than any lease this runner has
        # minted for it — the hub-node executor authored a fresh transition into a plain runner
        # node under its own ``hub_epoch = epoch + 1`` (an authored ``merged -> <node>`` land, #63, or
        # a conflict routed back to a worker node) while this runner retained the route. Spawn
        # into it, in place, in the warm environment.
        #
        # The epoch gate is load-bearing, not cosmetic: a chunk whose retries have just been
        # exhausted has enqueued its ``escalation.recorded`` fact to the *outbound buffer* but
        # not yet flushed it, so the hub still derives ``running`` for a beat — at the **same**
        # epoch this runner last minted. Firing on ``status == running`` alone would mistake that
        # for a hub advance and re-spawn the just-escalated node, which fails, escalates, and
        # loops forever. Only a strictly-higher hub epoch means "the hub moved the chunk, and this
        # runner has not spawned that node yet."
        _spawn_into_held_node(ctx, chunk_id)
    elif detail.status == ChunkStatus.DELIVERING:
        # A chunk parked at a hub node — the generic hub command node (#65/#66,
        # including its pending outcome). Drive it one step; a no-op at
        # the hub (slot busy, not yet due to poll, or not a hub-command node at all)
        # simply leaves this binding held, polled again next tick. This is the #66
        # re-drive path: a hub node deferred by slot contention, or parked pending,
        # had no other liveness poll before this wiring.
        _poll_hub_node(ctx, chunk_id)
    # An unresolved decision keeps waiting; the human's resolution is picked up on a
    # later tick. A chunk still delivering (a hub node, e.g. an open PR) keeps its
    # binding too — polled again next tick. A chunk whose escalation has not yet flushed
    # (hub still ``running``, same epoch) keeps its binding — the flush lands needs_human.


def _poll_hub_node(ctx: LoopContext, chunk_id: str) -> None:
    """Drive a chunk parked at a hub node one step via ``POST /chunks/{id}/hub-advance``
    (#65/#66) — the re-drive path a hub node otherwise has no liveness poll for.

    A no-op at the hub is expected and silent: the chunk is not currently parked at a
    generic hub command node, the fleet-wide serialization slot is held by a different
    chunk right now, or a
    prior ``pending`` outcome's ``poll_interval`` has not yet elapsed. Any of those
    leaves this runner's binding untouched — :func:`_advance_held_chunk` calls this
    again next tick. A transport failure is likewise swallowed: the hub is retried,
    not treated as a chunk-ending event.
    """
    try:
        ctx.hub.hub_advance(chunk_id)
    except HubClientError:
        return  # hub unreachable — retried next tick


def _spawn_into_held_node(ctx: LoopContext, chunk_id: str) -> None:
    """Spawn the held chunk's current node into its already-bound, warm environment.

    The hub already advanced the chunk — a landed-to-post-merge-node transition (#63)
    or a conflict routed back to a worker node — while this runner retained the route,
    so no active lease was minted for it and nothing else will spawn it. Mirrors
    :func:`_adopt_interrupted_claim`'s fetch-envelope-and-spawn shape."""
    bindings = ctx.store.bindings_for_chunk(chunk_id)
    if not bindings:
        _log.warning("held chunk advanced with no bound env — cannot spawn", chunk_id=chunk_id)
        return
    try:
        envelope = ctx.hub.get_envelope(chunk_id)
    except ChunkNotFoundError:
        _log.warning("hub reports advanced chunk unknown — releasing envs", chunk_id=chunk_id)
        _release_all(ctx, chunk_id)
        return
    except HubClientError:
        return  # hub unreachable — the transition is durable at the hub; retry next tick
    _log.info("hub advanced held chunk into a fresh node — spawning", chunk_id=chunk_id)
    _spawn_attempt(ctx, chunk_id, envelope, _bindings_as_environments(bindings), via="advance")


def _resolve_gate(ctx: LoopContext, chunk_id: str, decision: DecisionView) -> None:
    """Record the resolving transition for a decided gate and continue in place.

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
        artifacts=[],  # the decision's artifacts already landed
        decision_id=decision.decision_id,
        # issue #84a — not buffered (no enqueue_outbound here), so stamped directly at
        # submit; the same chunk-scoped write the buffered completion above stamps.
        route_token=ctx.store.route_token(chunk_id),
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

    Reads **``local_paused`` only**: the hub brake keeps its claims-only meaning
    and stays read in FILL alone. The local brake is the machine declining to work, and
    "start no processes on my machine" is a local statement, not a claims-only one — this
    is that gate's one shared home, called before every spawn primitive's first mutation
    so a suppressed spawn writes no fact, kills no pid, mints no lease, and elicits no
    verdict. The lease is left exactly as it was — active, unmodified — and the shape it
    is left in (an interrupted claim, an open resume intent, an open ask- or pause-park,
    an unjudged exit) is what the next tick's own recovery re-drives once the brake
    clears; no new state is needed here.

    Issue #45 shipped because the judgement resume was a spawn nobody had counted by hand;
    issue #46 added a fifth primitive the same way. ``tests/test_spawn_suppressed_registry.py``
    now holds that count mechanically, not this docstring: it AST-asserts every
    ``ctx.harness.spawn``/``ctx.harness.resume_with_message``/``ctx.harness.judge`` call site
    (:func:`_spawn_attempt`, :func:`_resume_in_place`, :func:`_resume_if_answered`,
    :func:`_resume_if_unpaused`, :func:`_advance_exited_worker`'s judgement resume) sits in a
    function that also calls this gate, so a sixth primitive of that shape fails the test by
    name instead of shipping ungated. Note what is deliberately *absent* even so:
    :func:`_kill_and_park_paused` is a kill, not a spawn, and a chunk-level pause from the
    hub is not this brake's business (see that function).

    ``chunk_id`` is always present; ``lease_id`` is ``None`` at :func:`_spawn_attempt` (the
    gate fires before a lease is minted) and carries the held/prior lease at restart-resume,
    answer-resume, pause-resume, and the judgement resume."""
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
    """Mint a fresh-epoch lease and spawn a headless worker for a node-step.

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
    # A per-lease capability token (issue #113, Phase 1): minted alongside the lease
    # itself, its hash stashed durably here, the plaintext carried forward only to
    # the spawn preamble (never persisted). Pure scaffold this phase — no caller yet
    # authorizes anything against `lease_token_hash`; a later attach endpoint is what
    # compares a presented token's hash against it.
    lease_token = secrets.token_urlsafe(_LEASE_TOKEN_BYTES)
    ctx.store.record_lease_token(lease_id, hash_token(lease_token), now)
    # The lease is a hub-bound fact: buffer it so the flusher reports it up to
    # POST /events, ahead of any completion minted under it (FIFO). It is the
    # fence input the hub's completion check consumes — the runner's mint keeps the
    # hub's latest epoch in lockstep across a build -> review chunk, and a requeue's mint
    # closes an escalation by supersession. Stamped with the chunk's stashed route
    # token (issue #84a) — present on every spawn path (fill, adopt, reclaim, requeue,
    # requeue-resume) since they all route through here; ``None`` only if no won claim
    # ever stashed one for this chunk.
    ctx.store.enqueue_outbound(
        kind=LEASE_MINTED,
        chunk_id=chunk_id,
        lease_id=lease_id,
        payload=json.dumps({"chunk_id": chunk_id, "epoch": epoch, "route_token": ctx.store.route_token(chunk_id)}),
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
        runner_prompt=ctx.config.runner_prompt,
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
        stdout_path=_stdout_path(ctx, lease_id, _pending_generation(ctx, lease_id)),
        lease_token=lease_token,
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
    """Close a failed attempt, then requeue at the node or escalate per the budget.

    The exhausted-retries branch checks ownership before escalating (blizzard#38). Tick order is
    REAP -> RESUME -> PULL -> FILL -> ADVANCE, and PULL's own detach sweep
    (:func:`_reconcile_leases`) is what abandons a lease the hub no longer routes here — but a
    caller earlier in the tick (REAP, chiefly) can reach an exhausted retry budget on such a
    lease first. Escalating anyway would buffer an ``escalation.recorded`` fact this same tick's
    PULL cannot retract once flushed — unlike the requeue branch, whose fresh, routeless lease is
    itself caught and abandoned by that later PULL pass, an escalation is a one-way door. So this
    branch re-asks the same ownership question :func:`_reconcile_leases` asks and, if the chunk is
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

    The hub confirms this runner holds the route and the runner holds the binding,
    but no lease was ever minted (the crash landed in FILL's claim→spawn window). Recovery
    is a spawn of the chunk's current node from its idempotent envelope into the
    already-bound environment — the same work FILL's tail would have done.

    Also the route-token recovery path (issue #84b): the crash window this adopts
    across spans the claim response too, so a runner that never read its route token
    back has no ``route_tokens`` row for this chunk. Re-keying before spawning fills
    it in from a fresh mint — the READY reclaim branch needs no equivalent (a fresh
    claim there already returns a fresh token in its own response).

    A 404 (:class:`ChunkNotFoundError`) here is terminal the same way it is for
    :func:`_advance_held_chunk` (blizzard#9): there is no active lease over this chunk to
    reap, only the binding this function already owns, so a chunk the hub no longer knows
    about is released the same way rather than retried forever."""
    bindings = ctx.store.bindings_for_chunk(chunk_id)
    if not bindings:
        _log.warning("adopt with no bound env — cannot spawn", chunk_id=chunk_id)
        return
    if ctx.store.route_token(chunk_id) is None:
        try:
            rekeyed = ctx.hub.rekey_route_token(chunk_id)
        except ChunkNotFoundError:
            _log.warning("hub reports adopted chunk unknown — releasing envs", chunk_id=chunk_id)
            _release_all(ctx, chunk_id)
            return
        except HubClientError:
            return  # hub unreachable — the binding is durable; retry next tick
        ctx.store.set_route_token(chunk_id, token=rekeyed.route_token, at=ctx.clock.now())
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


def _resume_requeued_chunk(ctx: LoopContext, chunk_id: str) -> None:
    """Spawn a fresh attempt at the chunk's current node — the human cleared its local
    needs_human hold (issue #53: ``blizzard runner requeue``).

    The hold-clearing fact was already durable before this runs (``RequeueService``
    records it fact-first, ``bzh:crash-correctness``); this is the next tick's own
    read-back of it, exactly mirroring :func:`_adopt_interrupted_claim`'s recovery shape —
    a chunk this runner already holds, spawned fresh into its warm environment. The
    retry budget is **carried, not reset**: this is an ordinary :func:`_spawn_attempt`
    mint, so :meth:`~blizzard.runner.store.repository.IReadRunnerStore.attempt_count`
    simply gains one more entry against the node's existing ``retries_max`` — a human
    requeue buys exactly one more try, not a fresh budget.

    A 404 (:class:`ChunkNotFoundError`) here is terminal the same way it is for
    :func:`_adopt_interrupted_claim` (blizzard#9): the hub no longer knows this chunk, so
    the held binding is released rather than retried forever."""
    bindings = ctx.store.bindings_for_chunk(chunk_id)
    if not bindings:
        _log.warning("requeue-resume with no bound env — cannot spawn", chunk_id=chunk_id)
        return
    try:
        envelope = ctx.hub.get_envelope(chunk_id)
    except ChunkNotFoundError:
        _log.warning("hub reports requeued chunk unknown — releasing envs", chunk_id=chunk_id)
        _release_all(ctx, chunk_id)
        return
    except HubClientError:
        return  # hub unreachable — the requeue fact is durable; retry next tick
    _log.info("resuming requeued chunk — spawning current node", chunk_id=chunk_id)
    _spawn_attempt(ctx, chunk_id, envelope, _bindings_as_environments(bindings), via="requeue-resume")


def _reclaim_interrupted(ctx: LoopContext, chunk_id: str, bindings: list[EnvBindingRecord]) -> None:
    """Complete a claim whose hub POST never landed — claim now, reusing the held binding.

    The runner bound the chunk's environment but crashed before (or during) the claim, so
    the hub still shows the chunk ``ready``. Rather than release and re-acquire (which would
    churn the environment and re-bind the same id), the runner claims the route with the
    environment it already holds and spawns on success; a 409 means another runner took the
    chunk while this one was down, so the binding is released."""
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
    if outcome.denied_paused is not None:
        # Same distinct outcome as FILL's own claim (issue #44) — the hub's registry
        # already has this runner paused, so the reclaim was refused outright rather
        # than lost to another runner.
        _log.info("interrupted claim denied — runner paused at the hub", chunk_id=chunk_id)
        _release_all(ctx, chunk_id)
        return
    if outcome.conflict is not None or outcome.claimed is None:
        _log.info("interrupted claim lost the race — releasing binding", chunk_id=chunk_id)
        _release_all(ctx, chunk_id)
        return
    _log.info("re-claimed interrupted chunk — spawning current node", chunk_id=chunk_id)
    # Same stash as FILL's own won claim (issue #84a) — a reclaim is a fresh claim, so
    # its token overwrites whatever this chunk_id's row held before (there should be
    # none yet on this path, but overwrite is correct either way: a fresh claim always
    # wins).
    ctx.store.set_route_token(chunk_id, token=outcome.claimed.route_token, at=ctx.clock.now())
    _spawn_attempt(ctx, chunk_id, outcome.claimed.envelope, envs, via="reclaim")


def _requeue(ctx: LoopContext, lease: LeaseRecord) -> None:
    """Re-attempt the node in the same environments — new session, new lease, fresh epoch.

    The prior attempt's lease is already closed by the caller (:func:`_fail_attempt`) before
    this runs, so a 404 (:class:`ChunkNotFoundError`) here leaves no active lease behind for
    PULL's own sweep (:func:`_reconcile_leases`) to find and clean up — reached, notably, from
    REAP, which precedes PULL in tick order, so PULL's sweep has not yet run this same chunk
    this tick. Left as a generic :class:`HubClientError`, this is the same held-forever shape
    issue #9 fixed for :func:`_reassigned_or_detached`, just for a chunk gone between the
    failed attempt and its requeue rather than between two ticks — so it is released here too."""
    bindings = ctx.store.bindings_for_chunk(lease.chunk_id)
    if not bindings:
        _log.warning("requeue with no bound env — cannot re-spawn", chunk_id=lease.chunk_id)
        return
    try:
        envelope = ctx.hub.get_envelope(lease.chunk_id)  # idempotent re-read
    except ChunkNotFoundError:
        _log.warning("hub reports chunk unknown at requeue — releasing envs", chunk_id=lease.chunk_id)
        _release_all(ctx, lease.chunk_id)
        return
    except HubClientError:
        return  # the closed attempt is durable; FILL/ADVANCE re-drives next tick
    _log.info("requeuing at node", chunk_id=lease.chunk_id, node=lease.node_name)
    _spawn_attempt(ctx, lease.chunk_id, envelope, _bindings_as_environments(bindings), via="requeue")


def _escalate(ctx: LoopContext, lease: LeaseRecord, *, reason: str = "retries exhausted") -> None:
    """Park the chunk needs-human at the hub, envs held for takeover.

    The escalation rides the outbound buffer as an ``escalation.recorded`` fact,
    flushed to the hub's POST /events, where the fleet derives ``needs_human``
    (an open escalation with no later lease mint). It carries the
    pasteable takeover command — ``cd <workdir> && <harness resume>`` composed from the
    adapter's session surface — so a human resumes the
    parked session in the agent's own warm worktrees; a requeue's later lease mint
    closes it by supersession. Environments stay bound throughout.

    ``reason`` is log-line prose only — every caller's escalation (retries-exhausted,
    :func:`_park_on_cost_cap`'s spend cap) rides the identical wire fact and takeover
    composition; only why it happened differs.
    """
    now = ctx.clock.now()
    bindings = ctx.store.bindings_for_chunk(lease.chunk_id)
    takeover = ""
    if lease.session_id is not None and bindings:
        takeover = ctx.harness.resume_command(bindings[0].workdir, lease.session_id)
    payload = json.dumps(
        {
            "chunk_id": lease.chunk_id,
            "epoch": lease.epoch,
            "takeover_command": takeover,
            "route_token": ctx.store.route_token(lease.chunk_id),  # issue #84a
        }
    )
    ctx.store.enqueue_outbound(
        kind=ESCALATION_RECORDED, chunk_id=lease.chunk_id, lease_id=lease.lease_id, payload=payload, created_at=now
    )
    _log.info(f"escalated to needs-human — {reason}", chunk_id=lease.chunk_id, takeover=takeover)


def _park_on_ask(ctx: LoopContext, lease: LeaseRecord, ask: AskRecord) -> None:
    """Park the chunk on a question: forward it to the hub and stop the reap clock.

    The worker asked and exited, so there is no live worker to judge or reap: the
    question rides the outbound buffer up to the hub (store-and-forward), where it
    becomes the durable row the chunk derives ``waiting_on_human`` from,
    and the local park fact keeps REAP off the dormant lease and ADVANCE from re-parking
    or eliciting a verdict. The env bindings stay held so the session is warm for
    the resume. No retry is consumed — a park is not a failed attempt.

    The spawn/resume invocation that asked-and-exited still burned real tokens, so its
    usage is recorded here (issue #58) before the park — the same honesty the judged
    exit gets. No judgement ran, so only the worker's own sample is recorded; keyed on
    ``(lease, generation, kind)`` it is idempotent across a re-park and distinct from the
    answer-resume generation's later fact, so an ask-and-answer round never double-counts.
    """
    now = ctx.clock.now()
    _record_worker_usage(ctx, lease, ctx.store.bindings_for_chunk(lease.chunk_id))
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
            "route_token": ctx.store.route_token(lease.chunk_id),  # issue #84a
        }
    )
    ctx.store.enqueue_outbound(
        kind=QUESTION_ASKED, chunk_id=lease.chunk_id, lease_id=lease.lease_id, payload=payload, created_at=now
    )
    ctx.store.record_park(lease_id=lease.lease_id, chunk_id=lease.chunk_id, question_id=ask.question_id, parked_at=now)
    _log.info("chunk parked on question", chunk_id=lease.chunk_id, question_id=ask.question_id)


def _resume_if_answered(ctx: LoopContext, lease: LeaseRecord) -> None:
    """Poll a parked lease's question; on an answer, resume the dormant session.

    The answer is a durable row at the hub, so this is crash-safe and re-runnable: while
    the question is unanswered the poll is a no-op and the reap clock stays stopped. Once
    answered, the agent is **reconstituted around the answer** — the same session, same
    lease, same node-step — via the adapter's resume-with-message. The lease's
    new pid is recorded so it reads live again, the park is closed, and ``answer.delivered``
    is buffered up to the hub (board detail; the status already flipped at question.answered).

    Gated by the local brake (issue #45) **before the poll** — this step's own ``get_question``
    poll runs none while the brake is on. That is not the same as the runner making no hub call
    that tick: :func:`_reconcile_leases` still polls ``ctx.hub.get_chunk`` once per active lease
    regardless of the local brake, deliberately ungated — a kill is not a spawn, and a chunk pause
    is a hub-level instruction orthogonal to this runner's own brake. A suppressed resume leaves
    the park open; the answer is picked up once the brake clears (no retry consumed either way).
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

    # The resume prompt reconstitutes the agent around the answer. The
    # human framing rides a leading comment line and the answer itself is the payload, so
    # the agent reads "who answered" as context and acts on the answer body — a shape the
    # blizzard-mock façade (prompt-is-program) executes directly, and a real harness reads
    # as ordinary resume text (the exact prose is unpinned).
    who = question.answered_by or "operator"
    message = f"# Answer from {who}. Continue.\n{question.answer}"
    pid = ctx.harness.resume_with_message(
        bindings[0].workdir,
        lease.session_id or "",
        message,
        stdout_path=_stdout_path(ctx, lease.lease_id, _pending_generation(ctx, lease.lease_id)),
        preamble=_resume_preamble(ctx, lease, bindings),
        chunk_id=lease.chunk_id,
    )
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


def _resume_if_unpaused(ctx: LoopContext, lease: LeaseRecord) -> None:
    """Poll a pause-parked lease's chunk; once the operator resumes it, restart its session (#46).

    The **fifth** member of the resume family, and a sibling of :func:`_resume_if_answered`
    rather than a branch inside it: the two share a silhouette but no body. That one polls
    ``get_question(park.question_id)`` — structurally impossible here, since a pause-park has no
    question — and they differ in every step besides: a different poll, a different message, a
    different resume fact, and no outbound. Parameterizing them on a boolean would produce one
    function with two disjoint halves.

    Same lease, same epoch, same session; only ``pid``/``process_start_time`` are
    rewritten via ``record_spawn``, so **no retry is consumed** — the pause cost the chunk a
    process, not an attempt. Modeled on :func:`_resume_if_answered` minus its kill-first: a
    pause-parked worker was already killed by :func:`_kill_and_park_paused`, so there is no
    survivor to fence.

    An **ask-parked** lease returns early even once unpaused. This is the other half of ADVANCE's
    pause-dominates ordering: the lease is dormant on a question *and* a pause, so lifting the
    pause must not conjure a resume out of an answer that may not exist. Clearing the pause-park
    hands it back to :func:`_resume_if_answered` on the next tick, which resumes it if and only
    if the question is actually answered.

    **Gated by the local brake before the poll**, like every other spawn primitive:
    ``resume_with_message`` below is a real spawn, and landing a fifth one outside
    :func:`_spawn_suppressed` is precisely how issue #45 happened. The gate sits above
    ``record_pause_park_resume`` so a suppressed resume writes **no fact** — the gate's stated
    contract — leaving the pause-park open for the first tick after the brake clears. That gate
    stops only this step's own ``get_chunk`` poll, not every hub call the tick makes:
    :func:`_reconcile_leases` still polls ``ctx.hub.get_chunk`` once per active lease regardless
    of the local brake, deliberately ungated — a kill is not a spawn, and a chunk pause is a
    hub-level instruction orthogonal to this runner's own brake.

    The ``resume_with_message`` → ``record_spawn`` gap is the same by-construction spawn-record
    window :func:`_resume_in_place` and :func:`_resume_if_answered` already carry un-armed: no
    crash point can arm a window whose recovery input — the new pid — does not yet exist. It is
    bounded to that one call-return→store-write gap (design/runner/loop.md)."""
    if _spawn_suppressed(ctx, via="pause-resume", chunk_id=lease.chunk_id, lease_id=lease.lease_id):
        return
    try:
        detail = ctx.hub.get_chunk(lease.chunk_id)
    except ChunkNotFoundError:
        # The chunk is gone outright — not this step's abandon to make: PULL's
        # `_reconcile_leases` owns it and runs ahead of ADVANCE this same tick.
        return
    except HubClientError:
        return  # hub unreachable — the park is durable; retry next tick
    if detail.pause is not None:
        return  # still paused — the reap clock stays stopped
    if detail.route is None or detail.route.runner_id != ctx.config.runner_id:
        return  # detached/reassigned while parked — PULL's sweep abandons it, not this step
    now = ctx.clock.now()
    if lease.lease_id in ctx.store.ask_parked_lease_ids():
        # Dormant on a question underneath the pause. Clearing the pause-park is the whole action:
        # the next tick's `_resume_if_answered` owns it, and an answer — not this resume —
        # restarts it.
        ctx.store.record_pause_park_resume(lease_id=lease.lease_id, resumed_at=now)
        _log.info("pause lifted on an ask-parked chunk — awaiting its answer", chunk_id=lease.chunk_id)
        return
    bindings = ctx.store.bindings_for_chunk(lease.chunk_id)
    if not bindings or lease.session_id is None:
        _log.warning("unpaused chunk has no warm env/session — cannot resume", chunk_id=lease.chunk_id)
        return
    # The un-armable spawn-record gap (see the docstring) — the same one SPAWN, restart-resume
    # and answer-resume carry, not a new one this step introduces.
    pid = ctx.harness.resume_with_message(
        bindings[0].workdir,
        lease.session_id,
        _PAUSE_RESUME_MESSAGE,
        stdout_path=_stdout_path(ctx, lease.lease_id, _pending_generation(ctx, lease.lease_id)),
        preamble=_resume_preamble(ctx, lease, bindings),
        chunk_id=lease.chunk_id,
    )
    ctx.store.record_spawn(
        lease.lease_id,
        pid=pid,
        process_start_time=ctx.process.start_time(pid) or "",
        session_id=lease.session_id,  # unchanged — same session under the same lease
        spawned_at=now,
    )
    ctx.store.record_pause_park_resume(lease_id=lease.lease_id, resumed_at=now)
    _log.info(
        "resumed dormant session after an operator unpause",
        chunk_id=lease.chunk_id,
        lease_id=lease.lease_id,
        epoch=lease.epoch,
        pid=pid,
    )


def _missing_produces(
    envelope: NodeEnvelope, git_artifacts: list[SubmittedArtifact], attachments: dict[str, str]
) -> list[str]:
    """Every `produces:` name this attempt covers with neither a pushed git commit nor
    an explicit attachment (issue #113, Phase 4) — the nudge-worthy set
    :func:`_advance_exited_worker` checks before submitting. Order follows the
    envelope's own `produces:` declaration, not attachment/git order, so the nudge
    message and a node's declared list read in the same sequence. Mirrors
    :func:`_collect_asset_artifacts`'s own git-coverage check rather than sharing it:
    the two run at different points in the same attempt (this one before the nudge,
    that one after), over ``attachments`` snapshots that may legitimately differ.

    The git-commit half of "covered" is the same predicate the hub's own backstop
    checks (:func:`~blizzard.hub.domain.produces_auth.check_produces`) — both call
    :func:`~blizzard.wire.completion.satisfied_produces_names` so the two coverage
    models cannot drift apart again."""
    covered = satisfied_produces_names(git_artifacts)
    return [name for name in envelope.node.produces if name not in covered and name not in attachments]


def _nudge_message(missing: list[str]) -> str:
    """The nudge resume's message (issue #113, Phase 4): one `#`-prefixed comment
    line naming every unattached `produces:` name and the CLI to answer it with —
    mirroring :data:`_PAUSE_RESUME_MESSAGE`'s shape, so the mock harness's
    prompt-is-program exec sees a legal no-op script while a real harness reads the
    same text as an ordinary resume instruction."""
    names = ", ".join(missing)
    return (
        f"# This node's `produces:` still needs an explicit submission for: {names}. "
        f"Before this attempt is judged done, run `blizzard runner attach --name <name>` "
        f"(content on stdin) for each name listed above."
    )


def _collect_asset_artifacts(
    envelope: NodeEnvelope,
    git_artifacts: list[SubmittedArtifact],
    assessment: str,
    attachments: dict[str, str],
) -> list[SubmittedArtifact]:
    """Emit an asset artifact per produced name no git commit covers.

    The engine has no file convention for assets: a node that declares it
    ``produces`` a name — the review node's ``findings`` — but pushes no git commit of
    that name emits an asset built from either an explicit attachment or the worker's
    judgement assessment. ``attachments`` is the lease's durable, newest-content-per-name
    submissions (``blizzard runner attach --name``, issue #113 Phase 2); a name present
    there wins over the assessment and is marked ``attached=True`` — the provenance a
    multi-asset node needs to tell its N distinct artifacts apart instead of aliasing
    them all to one assessment (#90). A name with no attachment falls back to the
    assessment as before, ``attached=False``. Git-commit artifacts are named by repo, so
    a build node producing repo commits yields no assets; a read-only review node yields
    its findings. Content may be empty (a clean pass) — the asset still lands, and only a
    fail routes it back into build (latest-by-epoch)."""
    from blizzard.hub.domain.artifacts import ArtifactKind

    covered = {a.name for a in git_artifacts}
    submitted: list[SubmittedArtifact] = []
    for name in envelope.node.produces:
        if name in covered:
            continue
        if name in attachments:
            submitted.append(
                SubmittedArtifact(name=name, kind=ArtifactKind.ASSET, content=attachments[name], attached=True)
            )
        else:
            submitted.append(SubmittedArtifact(name=name, kind=ArtifactKind.ASSET, content=assessment))
    return submitted


def _push_and_collect_artifacts(ctx: LoopContext, bindings: list[EnvBindingRecord]) -> list[SubmittedArtifact]:
    """Discover the produced git commits, push their branches, and name them."""
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
    """Release every held environment at the chunk's tenure end, and clean up every
    lease this chunk ever minted's per-generation usage-stdout files (issue #58) —
    bounded, one file per attempt ever made under each lease, no longer needed once
    its usage facts are durable."""
    now = ctx.clock.now()
    for binding in ctx.store.bindings_for_chunk(chunk_id):
        ctx.provider.release(binding.environment_id)
        ctx.store.record_release(chunk_id=chunk_id, environment_id=binding.environment_id, released_at=now)
    for lease_id in ctx.store.lease_ids_for_chunk(chunk_id):
        _cleanup_stdout(ctx, lease_id)


def _cleanup_stdout(ctx: LoopContext, lease_id: str) -> None:
    """Remove every one of a lease's per-generation stdout files, if any.

    Bounded to the durably recorded generation count (:meth:`IReadRunnerStore.
    lease_generation`) plus one: the un-armable spawn-record gap every resume site's
    docstring calls out (e.g. :func:`_resume_in_place`) can leave a file on disk for a
    generation whose own ``record_spawn`` never landed, so the ``+1`` also catches that
    one stray file. A missing file at any of those generations is a no-op — never
    redirected (``worker_stdout_dir`` unset), already cleaned up, or that ``+1`` slot
    was never actually written."""
    if not ctx.config.worker_stdout_dir:
        return
    for generation in range(1, ctx.store.lease_generation(lease_id) + 2):
        with contextlib.suppress(OSError):
            os.remove(_stdout_path(ctx, lease_id, generation))


def _release_acquired(ctx: LoopContext, acquired: list[AcquiredEnvironment]) -> None:
    """Release just-acquired (unbound) environments after a lost claim."""
    for a in acquired:
        ctx.provider.release(a.environment_id)


def _release_binding(ctx: LoopContext, chunk_id: str, acquired: list[AcquiredEnvironment]) -> None:
    """Undo a just-recorded binding whose claim never landed — release the fact and the env.

    The binding is written before the hub claim, so a claim that fails to send or
    loses the race must retract both the local binding fact and the provider allocation,
    leaving the chunk exactly as if it had never been touched (it stays ``ready``)."""
    now = ctx.clock.now()
    for a in acquired:
        ctx.store.record_release(chunk_id=chunk_id, environment_id=a.environment_id, released_at=now)
        ctx.provider.release(a.environment_id)


def _bindings_as_environments(bindings: list[EnvBindingRecord]) -> list[AcquiredEnvironment]:
    return [AcquiredEnvironment(environment_id=b.environment_id, workdir=b.workdir) for b in bindings]


def _elicitation_tail(envelope: NodeEnvelope) -> str:
    """The engine-generated ``<Choice>`` elicitation appended to the judgement prompt.

    Emitted as ``#``-prefixed lines so the tail is harness-agnostic: inert whether
    the judgement prompt is LLM prose (a comment block a real coding harness still
    reads) or a mock behavior *script* (the mock ``exec``s the prompt, and a bare
    prose tail would be a ``SyntaxError``).
    """
    lines = ["", "", "# Select exactly one outcome and reply with <Choice>name</Choice>:"]
    for choice in envelope.node.choices:
        lines.append(f"#   - {choice.name}: {choice.description}")
    return "\n".join(lines)
