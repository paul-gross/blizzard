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
import shlex
import uuid
from datetime import datetime, timedelta

from blizzard.foundation.crash import crashpoint
from blizzard.foundation.ids import LEASE_PREFIX, mint
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.domain.artifacts import ArtifactKind
from blizzard.hub.domain.graph import SessionMode
from blizzard.hub.domain.work import ChunkStatus
from blizzard.runner.domain.lease_auth import mint_lease_token
from blizzard.runner.domain.leases import as_utc, is_heartbeat_stale
from blizzard.runner.environments.provider import (
    AcquiredEnvironment,
    EnvironmentPreparationError,
    WorkspaceAcquisitionError,
)
from blizzard.runner.harness.adapter import HarnessSpawnError, WorkerPreamble
from blizzard.runner.harness.external_usage import ExternalSubscriptionUsageSnapshot
from blizzard.runner.harness.preamble import render_worker_preamble
from blizzard.runner.harness.spawn_cwd import resolve_spawn_cwd
from blizzard.runner.harness.usage import UsageKind, UsageSample
from blizzard.runner.loop.checks import DEFAULT_CHECK_TIMEOUT
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.hub import ChunkNotFoundError, HubClientError
from blizzard.runner.loop.internal.subprocess_worktree_git import WorktreeGitError
from blizzard.runner.loop.process import IProcessProbe
from blizzard.runner.store.repository import (
    AskRecord,
    BufferedFact,
    CheckResultRecord,
    EnvBindingRecord,
    GitCommitDeclarationRecord,
    IWriteRunnerStore,
    LeaseRecord,
    NewLease,
    PoolHead,
)
from blizzard.wire.completion import (
    CheckResult,
    CompletionSubmission,
    SubmittedArtifact,
    checks_gate_violated,
    produces_coverage,
)
from blizzard.wire.decision import DecisionSubmission, DecisionView
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse, NodeConfig, NodeEnvelope
from blizzard.wire.facts import (
    ANSWER_DELIVERED,
    ESCALATION_RECORDED,
    EVENT_RECORDED,
    EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED,
    LEASE_MINTED,
    QUESTION_ASKED,
    RUNNER_LOCALLY_PAUSED,
    RunnerFact,
    RunnerFactBatch,
)
from blizzard.wire.graph import ProducesEntry
from blizzard.wire.queue import QueuePeekEntry
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
    "sample_external_subscription_usage",
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

# The env count a chunk gets when nothing says otherwise. One environment is the
# default, not an assumption baked into everything downstream: git-commit declarations
# are keyed by ``(lease, environment_id, repo)``, the ADVANCE harvest reads every bound
# environment, and delivery refuses an unconverged set rather than tie-breaking it — so
# a chunk holding several is representable and safe today. What is missing is only a
# *producer*: nothing in the queue entry or the chunk yet says how many a piece of work
# wants, so inventing a knob here would be a setting no caller sets.
_DEFAULT_ENV_COUNT = 1

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

# ADVANCE — judge an exited worker: verify declared git commits -> elicit verdict ->
# buffer completion. Verify is read-only (issue #143, Phase 4) — the push-mutation
# window this used to open (`advance.before-artifact-push` / `.after-artifact-push.
# before-judgement`) is gone; a read-only re-derivation needs no crash point of its own
# (`bzh:crash-correctness` — recorded as a removed exemption in
# `blizzard-context:/architecture/crash-correctness.md`).
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

# ADVANCE's checks-at-exit (issue #114): the runner runs a node's `checks:` at worker exit,
# before the judgement, and records each result as a durable fact, then a marker. The
# ordering (result rows → marker) is what makes the recorded results exactly-once across a
# crash. `after-results.before-marker` is the recovery-critical window the exactly-once-
# recording guarantee rests on: the rows are durable but the marker is not, so recovery
# finds `checks_ran` unset and safely re-runs (latest-wins). `after-marker.before-judge` is
# reached once the marker is durable and before the judgement is elicited: recovery finds
# `checks_ran` set and reads the recorded results back rather than re-running.
_CP_CHECKS_AFTER_RESULTS = crashpoint(
    "checks.after-results.before-marker",
    "check result rows durable; the checks-ran marker has not been written yet",
)
_CP_CHECKS_AFTER_MARKER = crashpoint(
    "checks.after-marker.before-judge",
    "checks-ran marker durable; the judgement has not been elicited yet",
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


def _stderr_path(ctx: LoopContext, lease_id: str, generation: int) -> str:
    """This lease's per-generation harness-**stderr** redirect target (issue #125, change
    L(iii)), or ``""`` for no redirect — the sibling of :func:`_stdout_path`, so a launched
    worker that crashed to stderr leaves a readable tail for the ``worker-lost`` event
    instead of the old ``DEVNULL`` discard."""
    if not ctx.config.worker_stdout_dir:
        return ""
    return os.path.join(ctx.config.worker_stdout_dir, f"{lease_id}.{generation}.stderr")


def _stderr_tail(ctx: LoopContext, lease: LeaseRecord, *, limit: int = 2000) -> str:
    """The tail of this lease's most-recent captured spawn-stderr (change L(iii)), or ``""``.

    Best-effort and never raises (a hung-but-live worker that never crashed to stderr, or a
    test with no ``worker_stdout_dir``, is the ordinary empty case) — folded into a
    `_fail_attempt` event's detail so a dead worker's last words reach the operator."""
    generation = ctx.store.lease_generation(lease.lease_id)
    if generation <= 0:
        return ""
    text = _read_stdout(_stderr_path(ctx, lease.lease_id, generation))
    return text[-limit:] if text else ""


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
    # Attribute to the lease's own `resolved_model` stamp (issue #144), not the adapter's
    # single default: per-session resolution means a judge turn on a sonnet session would
    # otherwise book its spend against opus. `None` (a lease predating the stamps) leaves
    # the adapter's default standing — the pre-#144 behavior, unchanged.
    judge_sample = ctx.harness.parse_usage(judge_output, "judge", model=lease.resolved_model)
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
    # Same attribution fallback as the judge fact above (issue #144): the lease's own
    # stamp, which on a resume is what the session was MINTED with rather than what a
    # fresh resolution would produce now.
    sample = ctx.harness.parse_usage(output, kind, model=lease.resolved_model) if output else None
    if sample is not None:
        return sample
    if ctx.transcripts is None or lease.session_id is None:
        return None
    fallback_workdir = bindings[0].workdir if bindings else None
    spawn_cwd = resolve_spawn_cwd(ctx.config.workspace_root, fallback_workdir)
    lines = ctx.transcripts.read_raw_lines(lease.session_id, spawn_cwd=spawn_cwd)
    if not lines:
        return None
    return ctx.harness.sum_transcript_usage(lines, kind, model=lease.resolved_model)


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
    * it is **not stale** *as measured at crash time* — it was actively working when
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

    Issue #150 widened that staleness baseline to include the lease's newest **spawn**
    (:func:`~blizzard.runner.domain.leases.last_activity`), and this classifier inherits the
    reclassification **deliberately**: a worker respawned shortly before the daemon died, whose
    heartbeats all belong to an earlier generation, was read as "stalled at crash time" and
    abandoned to a fresh retry. It was in fact working — it had just not made its first tool
    call of the new generation yet, the same blind spot the reap bug turns on. It now marks for
    resume, which is what its live-at-crash-time process warranted all along. The skip still
    fires for what it was written for: a worker whose newest spawn *and* newest beat both
    predate ``crashed_at`` by more than the threshold really had wedged.

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
    lease_token, token_hash = mint_lease_token()
    ctx.store.record_lease_token(lease.lease_id, token_hash, ctx.clock.now())
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
        # Reasserted, not sticky (issue #144) — see the judge call site's note.
        effort=lease.resolved_effort,
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
    ``released`` (not a failed attempt — it never gets to run) and the intent is cleared. Any open ask
    park for this lease is retired alongside — this path is reached for a parked lease too (the
    answer-driven resume, :func:`_resume_if_answered`, is the only other ``park_resumes`` writer), and
    without it the ask would show open in ``blizzard runner status``/``GET /asks`` forever
    (blizzard#202). ``via`` names which caller reached the ownership check that led here
    (``"resume"`` — restart-resume, ``"pull"`` — a live tick's :func:`_reconcile_leases`, ``"reap"`` —
    an escalation REAP suppressed in favor of this abandon, see :func:`_fail_attempt`) so the log
    line below does not overclaim a single cause."""
    now = ctx.clock.now()
    if lease.pid is not None and not killed:
        ctx.process.kill(lease.pid)
    _CP_ABANDON_AFTER_KILL.reached()  # worker killed; envs not yet released — recovery is the next tick's re-scan
    _release_all(ctx, lease.chunk_id)
    park = ctx.store.open_park(lease.lease_id)
    if park is not None:
        ctx.store.record_park_resume(lease_id=lease.lease_id, question_id=park.question_id, resumed_at=now)
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
    """Push one buffered fact to POST /events — the generic default arm for every kind
    but completion and decision (``lease.minted``, ``escalation.recorded``,
    ``question.asked``, the local pause/resume pair, ``usage.recorded``,
    ``event.recorded``, and ``external_subscription_usage.sampled`` among them)."""
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
        elif detail.route is None:
            # No live route, in a status that is neither `ready` (claimable) nor `running`
            # (ours to adopt) — e.g. stopped or detached hub-side (blizzard#202). Release
            # explicitly instead of matching no branch and leaking the binding forever.
            _log.info(
                "releasing binding — hub reports no live route in a non-ready, non-running state",
                chunk_id=chunk_id,
                hub_status=str(detail.status),
            )
            _release_all(ctx, chunk_id)


def _environments_wanted(entry: QueuePeekEntry) -> int:
    """How many environments this queue entry's chunk should be acquired.

    The single place the count is decided, so raising it above one is a change here
    rather than an audit of everything that once assumed a lone binding. Returns
    :data:`_DEFAULT_ENV_COUNT` for now because no producer names a number yet — the
    demand ("this work wants N environments") is a property of the work, so it belongs on
    the queue entry or the chunk, and neither carries it."""
    del entry  # no per-chunk demand signal exists yet
    return _DEFAULT_ENV_COUNT


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
        acquired = ctx.provider.acquire(entry.chunk_id, _environments_wanted(entry), held)
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
        # Surface the captured env-prep failure (issue #125, change L(i)) — no lease exists
        # yet (the chunk is not claimed), so it is a chunk-scoped `command-failed`. Then
        # return False (the chunk waits for a fixed workspace) exactly as before.
        _emit_command_failed(
            ctx,
            chunk_id=entry.chunk_id,
            lease_id=None,
            node_name=None,
            command=f"winter env-prep step: {exc.step}",
            stderr_tail=str(exc),
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
    resume_from = _resolve_session(
        ctx,
        entry.chunk_id,
        outcome.claimed.envelope.node,
        resolve_spawn_cwd(ctx.config.workspace_root, acquired[0].workdir if acquired else None),
    )
    _spawn_attempt(ctx, entry.chunk_id, outcome.claimed.envelope, acquired, via="fill", resume_from=resume_from)
    return True


# --------------------------------------------------------------------------- #
# ADVANCE
# --------------------------------------------------------------------------- #


def advance(ctx: LoopContext) -> None:
    """Judge finished workers and move chunks through the graph.

    Two responsibilities: (a) a session-bearing worker whose process has exited is a
    done declaration — resume it with the judgement prompt, parse the ``<Choice>``,
    verify its declared artifacts, and **buffer** the epoch-fenced completion (the flusher in
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


def _run_or_read_checks(
    ctx: LoopContext, lease: LeaseRecord, envelope: NodeEnvelope, bindings: list[EnvBindingRecord]
) -> list[CheckResultRecord]:
    """Run the node's ``checks:`` at worker exit and record them as durable facts, or read
    the recorded results back on a re-drive (issue #114).

    Empty for a node with no ``checks:`` (every packaged graph today). Otherwise: if
    ``checks_ran(lease, epoch)`` is unset, run each check in
    ``join(binding.workdir, node.checks_cwd)`` under ``node.checks_timeout``, record the
    result rows, then the marker; else read the recorded results back. Ordering (rows →
    marker) is what makes the recorded results exactly-once across a crash.

    **The re-run key is ``(lease, epoch)`` and never anything stable across a node re-entry**
    (e.g. ``(chunk, node)``). The verified runner lifecycle: a verdict-less retry, a
    ``requires_checks`` gate-fire, and a node re-entry each mint a *new* ``(lease, epoch)``
    via ``_spawn_attempt`` (a fresh lease + an incremented epoch), so ``checks_ran`` is
    unset and checks re-run against the rebuilt tree — correct. The only
    same-``(lease, epoch)`` re-drives are the hub-unreachable re-tick (ADVANCE returns
    before this function is reached, tree untouched) and the produces-nudge (which runs
    *after* the judgement, declares already-authored work, and must not author new tree
    content — see the nudge site). Keying on ``(chunk, node)`` would wedge every retry on a
    stale red result.

    Multi-env chunks are parked (a solo chunk holds exactly one env today), so checks run in
    the single leased binding. Were multi-env to land, checks would run per binding and a
    check is red if it fails in any — a deferral consistent with the parked K>1 batching.
    """
    node = envelope.node
    if not node.checks:
        return []
    if ctx.store.checks_ran(lease.lease_id, lease.epoch):
        return ctx.store.check_results_for_lease(lease.lease_id, lease.epoch)
    if ctx.check_runner is None:
        # The seam is unwired but the node declares checks — a composition-root/test-wiring
        # bug, never a production path (the daemon always wires SubprocessCheckRunner, and no
        # packaged graph declares checks). Surface it loudly and skip rather than wedge the
        # tick; a test that forgot to wire the fake sees its own gating assertions fail.
        _log.error(
            "node declares checks but no check-runner seam is wired — skipping checks",
            node=node.node_name,
            lease_id=lease.lease_id,
        )
        return []
    cwd = os.path.join(bindings[0].workdir, node.checks_cwd) if node.checks_cwd else bindings[0].workdir
    timeout = node.checks_timeout or DEFAULT_CHECK_TIMEOUT
    results: list[CheckResultRecord] = []
    for command in node.checks:
        outcome = ctx.check_runner.run(command, cwd, timeout)
        results.append(CheckResultRecord(command=command, passed=outcome.passed, output_tail=outcome.output_tail))
    # Rows first, then the marker — the ordering the crash points bracket and the
    # `runner:checks-recorded-when-marked` invariant rests on.
    ctx.store.record_check_results(
        lease_id=lease.lease_id,
        chunk_id=lease.chunk_id,
        node_id=lease.node_id,
        epoch=lease.epoch,
        results=results,
        at=ctx.clock.now(),
    )
    _CP_CHECKS_AFTER_RESULTS.reached()
    ctx.store.record_checks_ran(lease_id=lease.lease_id, epoch=lease.epoch, at=ctx.clock.now())
    _CP_CHECKS_AFTER_MARKER.reached()
    _log.info(
        "checks executed",
        node=node.node_name,
        count=len(results),
        red=sum(1 for r in results if not r.passed),
        lease_id=lease.lease_id,
    )
    return results


def _advance_exited_worker(ctx: LoopContext, lease: LeaseRecord) -> None:
    """Park on an open ask, else elicit the verdict and buffer the completion.

    The judgement elicitation (below) is a real spawn — :meth:`ctx.harness.judge` resumes
    the exited worker's session headlessly to capture its verdict reply — so it is gated
    by the local brake (issue #45) the same as the other three primitives, just placed
    later in this function: the ask-park and gate-decision branches above it end the
    attempt with no process started (a park or a human decision, not a judgement), and
    the artifact verify is a read-only re-derivation, not a spawn, so none of those need
    the gate. Only the judge call does. A suppressed judgement leaves the lease exactly as it
    was — active, session-bearing, dead pid, no completion buffered — so ADVANCE retries
    it every tick until the brake clears, the same self-driving shape every other gate in
    this module leaves behind.

    Checks (issue #114): the node's ``checks:`` run at worker exit, before the judgement is
    elicited (so their results inject into the judge prompt), and a ``requires_checks``
    choice selected while any check is red is treated like an unparseable verdict — a
    retry-consuming failure that re-queues a fresh rebuild, never an accepted edge (AC #4).
    A red check reported through a non-gated choice (``fail``) routes normally and never
    runs the gate (AC #5); a node with no ``requires_checks`` choice injects results but
    gates nothing (AC #6).
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

    # 1. Read back the worker's declared git commits and confirm each, read-only,
    #    against the forge — no push, no residue inference (issue #143, Phase 4). Not a
    #    harness spawn, so this runs regardless of the local brake; unlike the push it
    #    replaces, a failed verify is never re-raised — a read-only re-derivation opens
    #    no unsafe mutation window, so a captured failure is surfaced informationally
    #    and the declaration is simply treated as unverified (drives the Phase-2 nudge)
    #    rather than crash-looping the tick.
    artifacts, declared_this_attempt = _verify_and_collect_git_commits(ctx, lease, bindings)

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
    #    park/gate/verify work above (none of it a spawn) still happens while paused.
    if _spawn_suppressed(ctx, via="advance", chunk_id=lease.chunk_id, lease_id=lease.lease_id):
        return

    # 1c. Run the node's `checks:` at worker exit (issue #114), before the judgement is
    #     elicited — against the tree the worker just left, the same tree its judgement
    #     and the gate are rendered on. Durable facts keyed `(lease, epoch)`: a runner
    #     kill between check-run and judgement resumes at the right point without
    #     re-running or losing results. Empty for a node with no `checks:` (every packaged
    #     graph today) — the injection and the gate below are then no-ops. Not a harness
    #     spawn, so it runs after the local-brake gate above but is not itself gated.
    check_records = _run_or_read_checks(ctx, lease, envelope, bindings)

    # A dead worker whose session cannot answer a parseable <Choice> is a failure.
    # The check results (issue #114) ride between the authored judgement prose and the
    # `<Choice>` elicitation tail, so the worker judges against mechanical truth. Empty for
    # a node with no checks — then this adds nothing (AC #6 injection-only-when-present).
    prompt = (envelope.judgement_prompt or "") + _checks_block(check_records) + _elicitation_tail(envelope)
    # The adapter works in a directory; the runner resolves the provider-returned
    # workdir from the binding and supplies it. The judgement turn attaches its own
    # `retrospective`, so it carries the re-minted lease identity — the worker is
    # already dead (kill-then-resume), so invalidating its token orphans nothing.
    output = ctx.harness.judge(
        bindings[0].workdir,
        lease.session_id,
        prompt,
        preamble=_resume_preamble(ctx, lease, bindings),
        chunk_id=lease.chunk_id,
        # Reassert the lease's own stamped effort (issue #144) — effort is NOT
        # session-sticky, so a resume that omits it silently drops the declared value back
        # to the operator's ambient default. The model is deliberately absent: it IS
        # sticky, and the stamp rides along only to attribute this turn's usage.
        effort=lease.resolved_effort,
        model=lease.resolved_model,
    )

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

    # 2·checks-gate (issue #114): a `requires_checks` choice may NOT be taken while any
    # check is red. Evaluated immediately after parse_verdict and BEFORE the nudge, so it
    # judges the exact checks the worker was shown and judged against (the pre-nudge tree) —
    # runner-local gate and worker can never diverge on "the tree". A violation is treated
    # like an unparseable verdict: `_fail_attempt` consumes a retry and re-queues a FRESH
    # rebuild attempt under a NEW (lease, epoch) — the same path a verdict-less exit takes,
    # NOT an in-place re-judge of this session. The next worker rebuilds and re-runs checks,
    # and the red evidence reaches it through the Phase-3 injection on ITS own exit. A worker
    # that keeps selecting the gated choice against a check it cannot get green burns its
    # retry budget to needs_human; one that instead selects the non-gated `fail` routes to
    # the fix path normally and never runs this gate — the intended AC #4/#5 shape. This is
    # never an engine override of the worker's routing authority: the worker still chooses;
    # the engine only refuses to accept a green-gated edge over red mechanical truth.
    selected = next((c for c in envelope.node.choices if c.name == choice), None)
    if selected is not None and checks_gate_violated(selected.requires_checks, check_records):
        _log.warning(
            "requires_checks choice selected with a red check — failing attempt",
            chunk_id=lease.chunk_id,
            lease_id=lease.lease_id,
            choice=choice,
        )
        _fail_attempt(ctx, lease, reason=_FAILED, via="advance")
        return

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
            missing=[spec.name for spec in missing],
            lease_id=lease.lease_id,
            epoch=lease.epoch,
        )
        ctx.store.record_nudge_fired(lease_id=lease.lease_id, epoch=lease.epoch, at=ctx.clock.now())
        _CP_NUDGE_AFTER_FIRED_FACT.reached()
        # Checks-invariant (issue #114): this nudge runs AFTER the judgement (and, in
        # Phase 4, after the checks gate), and checks are deliberately NOT re-run on it. It
        # is safe because a nudge declares work `checks:` already evaluated at worker exit —
        # it must not author new tree content. The path already re-verifies only git
        # artifacts (below), never any deeper property, so checks-staleness-across-a-nudge is
        # bounded exactly as the `produces` backstop already is. Any future change that lets
        # a nudge author new tree content owns re-running checks here.
        # `judge`, not `resume_with_message`, on purpose: this call's own reply is
        # discarded (the nudge elicits no verdict of its own — the original judgement
        # above already stands), but the resume must still be *synchronous* — the
        # `attachments_for_lease` re-read just below has to observe whatever the worker
        # attached while the nudge ran, and only `judge`'s synchronous session-resume
        # guarantees the worker has already replied (and so had the chance to attach)
        # before this function reads on. `resume_with_message` only returns a new pid
        # (issue #113, Phase 4) — it would race the re-read against a worker still
        # composing its attach.
        # This second `_resume_preamble` deliberately re-mints again, invalidating the
        # primary judgement's token — safe only because `judge` is synchronous, so that
        # turn's attach has already completed. Do not hoist or share the two preambles.
        nudge_output = ctx.harness.judge(
            bindings[0].workdir,
            lease.session_id,
            _nudge_message(missing),
            preamble=_resume_preamble(ctx, lease, bindings),
            chunk_id=lease.chunk_id,
            effort=lease.resolved_effort,
            model=lease.resolved_model,
        )
        _CP_NUDGE_AFTER_RESUME.reached()
        # Record this invocation's own usage (issue #58) — a distinct `nudge` kind so it
        # cannot collide with (or be mistaken for) the primary judgement's own `judge`
        # fact already recorded above at this same generation (`_record_attempt_usage`);
        # the generation itself does not advance for this resume (no `record_spawn`
        # call — the pid is unchanged), so it is read fresh here rather than threaded
        # through from `_record_attempt_usage`, but resolves to the same value.
        nudge_generation = ctx.store.lease_generation(lease.lease_id)
        nudge_sample = ctx.harness.parse_usage(nudge_output, "nudge", model=lease.resolved_model)
        if nudge_sample is not None:
            _store_usage(ctx, lease, generation=nudge_generation, sample=nudge_sample)
        # Re-read: a worker that attached during the nudge must have its content picked
        # up before assembly below, not the assessment fallback it just corrected.
        attachments = ctx.store.attachments_for_lease(lease.lease_id)
        # Re-verify: a worker nudged for a missing `git_commit` spec may push and
        # declare (`blizzard runner artifact commit`) during the nudge, same as it may
        # attach an asset — symmetric with the attachments re-read just above, else
        # that declaration lands durably in the store but this attempt's completion
        # buffers without it (issue #143 re-review). Re-derives the lease's full
        # declaration set fresh, so overlay by repo name rather than append: any repo
        # whose declaration is unchanged from the pre-nudge pass (`declared_this_attempt`)
        # is skipped entirely — it was already resolved this attempt, verified or not,
        # so a broken `--repo` the worker never fixed surfaces exactly once rather than
        # re-emitting a duplicate `command-failed` — and a repo the worker re-declared
        # (amended) mid-nudge is re-verified fresh. The overlay itself is keyed by repo
        # name: pre-nudge artifacts seed the dict, post-nudge results (only ever
        # verified successes) overlay on top, so a transient re-verify hiccup here can
        # never regress an artifact this attempt already has, while a genuine amendment
        # wins.
        post_nudge_artifacts, _ = _verify_and_collect_git_commits(
            ctx, lease, bindings, already_declared=declared_this_attempt
        )
        by_repo = {a.name: a for a in artifacts}
        by_repo.update({a.name: a for a in post_nudge_artifacts})
        artifacts = list(by_repo.values())

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
        # The runner-executed check facts (issue #114) — carrying `(command, passed)` only;
        # `output_tail` stays runner-local (the store), off the wire [MF3]. The hub's
        # `requires_checks` backstop gates on this.
        check_results=[CheckResult(command=r.command, passed=r.passed) for r in check_records],
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
        resume_from = _resolve_session(
            ctx,
            chunk_id,
            next_envelope.node,
            resolve_spawn_cwd(ctx.config.workspace_root, envs[0].workdir if envs else None),
        )
        _spawn_attempt(ctx, chunk_id, next_envelope, envs, via="apply-response", resume_from=resume_from)
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
    held = _bindings_as_environments(bindings)
    resume_from = _resolve_session(
        ctx,
        chunk_id,
        envelope.node,
        resolve_spawn_cwd(ctx.config.workspace_root, held[0].workdir if held else None),
    )
    _spawn_attempt(ctx, chunk_id, envelope, held, via="advance", resume_from=resume_from)


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


def _resolve_session(ctx: LoopContext, chunk_id: str, node: NodeConfig, spawn_cwd: str | None) -> str | None:
    """The prior session id a node-entry spawn should resume, or ``None`` to mint fresh
    (issues #115, #144).

    **Only the resume-vs-mint decision.** The model and effort a spawn runs under are
    resolved unconditionally inside :func:`_spawn_attempt`, which is the sole funnel and
    so the only place that reaches *every* caller — node entry, retry, adopt, reclaim,
    and requeue-resume alike. Splitting them that way is what keeps "which session" (a
    node-entry question) from being confused with "under what configuration" (a question
    every spawn has).

    Reads only ``node.session``/``node.session_source`` — never the retry budget or
    attempt count, so a within-node retry (which never calls this) stays fresh (Q3).

    Three cases, in the order the reference vocabulary resolves:

    * ``FRESH`` — always ``None``. ``fresh:<name>`` is a *forced rotation point*: it mints
      a head that a later ``resume:<name>`` member continues, so a cyclic graph re-entering
      the node starts each iteration clean.
    * ``RESUME`` naming a **declared session** (``session_name`` set, #144) — the pool's
      head, subject to the rotation check.
    * ``RESUME`` naming a node, or bare (#115) — unchanged: that node's most-recent
      session, or the chunk's most-recent overall.

    No match anywhere falls back to fresh rather than erroring — a resume target is
    best-effort, not a hard requirement (AC4).

    ``spawn_cwd`` is threaded from the caller for the rotation check's transcript read: it
    is the tie-break hint a multi-match transcript glob needs, not the lookup key. Every
    node-entry site already holds what it needs to compute it.
    """
    if node.session is SessionMode.FRESH:
        return None
    if node.session_name is not None:
        return _resume_pool_head(ctx, chunk_id, node, spawn_cwd)
    return ctx.store.latest_session_id(chunk_id, node.session_source)


def _resume_pool_head(ctx: LoopContext, chunk_id: str, node: NodeConfig, spawn_cwd: str | None) -> str | None:
    """The named pool's head if it is still resumable, else ``None`` to mint a new one."""
    pool = node.session_name or ""
    head = ctx.store.pool_head(chunk_id, pool)
    if head is None:
        return None  # an empty pool — this member mints the head
    breach = _rotation_breach(ctx, head, node, spawn_cwd)
    if breach is None:
        return head.session_id
    _log.info(
        "rotating session pool",
        chunk_id=chunk_id,
        session_pool=pool,
        breached=breach,
        old_session_id=head.session_id,
    )
    return None


def _rotation_breach(ctx: LoopContext, head: PoolHead, node: NodeConfig, spawn_cwd: str | None) -> str | None:
    """Why this pool head must not be resumed, or ``None`` when it may be (issue #144).

    A head is resumed only while every *readable* declared threshold is under bound **and**
    its stamped model still matches the pool's currently-resolved model. Anything else
    mints a fresh head, which is where a model change takes effect: a cross-model resume is
    thereby structurally impossible, and the re-ingest cost of a model change is paid
    exactly where a fresh context is being built anyway.

    **An unreadable signal does not force rotation.** A threshold whose measurement comes
    back ``None`` — no usage fact yet, no transcript file, a transcripts seam that is not
    wired — is *not measured*, and a missing measurement is not a breach. Rotating on it
    would make every freshly minted head immediately ineligible, which is the opposite of
    what a bound is for.

    Returns the breached threshold's name (or ``model-drift``) so the caller can log which
    one fired, rather than a bare bool that leaves an operator guessing.
    """
    # Model drift first: it is the one check that needs no telemetry, and a pool whose
    # declaration was edited mid-chunk should rotate on the next member regardless of how
    # much context the old head had accumulated.
    resolved = ctx.harness.resolve_model(node.session_model) if node.session_model else None
    if resolved is not None and head.resolved_model is not None and head.resolved_model != resolved:
        return "model-drift"

    rotate = node.session_rotate
    if rotate is None:
        return None  # the declaration bounds nothing

    if rotate.max_context_tokens is not None:
        tokens = ctx.store.session_context_tokens(head.session_id)
        if tokens is not None and tokens > rotate.max_context_tokens:
            return "max_context_tokens"

    # A count is never an unknown — it is the number of rows that exist.
    if (
        rotate.max_invocations is not None
        and ctx.store.session_invocation_count(head.session_id) > rotate.max_invocations
    ):
        return "max_invocations"

    if rotate.max_transcript_bytes is not None and ctx.transcripts is not None:
        # `ctx.transcripts` is `| None` (wired at both real construction sites, absent only
        # in a test context); treat that absence as unreadable too, exactly like a missing
        # file — not as a zero that would make the threshold silently inert.
        size = ctx.transcripts.size_bytes(head.session_id, spawn_cwd=spawn_cwd)
        if size is not None and size > rotate.max_transcript_bytes:
            return "max_transcript_bytes"

    return None


def _resolve_model_and_effort(
    ctx: LoopContext, chunk_id: str, node: NodeConfig, resume_from: str | None
) -> tuple[str | None, str | None]:
    """The model and effort this spawn runs under, and stamps (issue #144).

    **The stamp describes the session, not the preference.** On a spawn that *resumes*,
    both are **inherited** from the resumed session's own most recent stamp rather than
    freshly resolved — because the running process keeps whatever it was minted with, and
    recording the fresh preference would make the fact false wherever a node resumes a
    session it did not mint.

    That is not hypothetical. In the graph this change tunes, `retrospective` carries no
    `session:` line (bare `resume`) and so is not a pool member. Entering it resumes the
    `code` pool's sonnet session and passes no `--model`, so the process runs sonnet —
    while a fresh resolution for that node finds no declaration, no chunk default, and
    falls to the runner default, opus. Stamping the preference would then book opus spend
    against a sonnet session, and hand a takeover command that appends `--model opus` to a
    live sonnet session, flipping it on an operator. That is precisely the outcome
    mint-only exists to prevent.

    On the pooled path the drift check guarantees stamped == resolved anyway, so inheriting
    is uniform rather than a special case. A resumed session whose own stamp is ``None`` (a
    lease predating the stamps) inherits ``None`` — *unknown*, which every consumer
    declines to guess at rather than substituting a default.

    Effort is inherited alongside model even though it is reasserted on every invocation
    (it is not sticky): the two describe one session's configuration, and a resume that
    reasserted a *different* effort than the session was minted with would be the same
    kind of lie in the other direction.
    """
    if resume_from is not None:
        prior = ctx.store.lease_for_session(resume_from)
        return (prior.resolved_model, prior.resolved_effort) if prior is not None else (None, None)
    model = ctx.harness.resolve_model(node.session_model)
    return (model, ctx.harness.resolve_effort(node.session_effort))


def _spawn_attempt(
    ctx: LoopContext,
    chunk_id: str,
    envelope: NodeEnvelope,
    environments: list[AcquiredEnvironment],
    *,
    via: str,
    resume_from: str | None = None,
) -> None:
    """Mint a fresh-epoch lease and spawn a headless worker for a node-step.

    Always its caller's final statement, with no post-spawn logic after it (fill/apply-
    response/adopt/reclaim/requeue) — that is what lets the local-pause gate below stay a
    silent ``None`` return indistinguishable from a real spawn (issue #45): there is no
    boolean a caller could misread as "spawn failed" and burn a retry on. A future caller
    that adds post-spawn logic must re-read this contract first. ``via`` names the calling
    site, attributing the gate's suppression log line to it.

    ``resume_from`` (issue #115) is the prior session id this spawn continues, or
    ``None`` for a fresh session — the sole funnel into ``ctx.harness.spawn``, so a
    node-entry resume rides the same :func:`_spawn_suppressed` gate every other spawn
    does (AC5): a suppressed spawn resolves and mutates nothing, resume target included.
    Only node-entry callers (:func:`_fill_one`, :func:`_apply_response`'s ``NEXT``
    branch, :func:`_spawn_into_held_node`) compute a non-default value via
    :func:`_resolve_session`; every other caller (retry, adopt, reclaim, requeue-resume)
    leaves it at the default ``None``, i.e. always fresh.

    **Model and effort are resolved here, unconditionally** (issue #144), because this is
    the sole funnel and therefore the only place that reaches *every* caller. There is no
    separate fallback for the non-node-entry paths: a retry spawns under the same declared
    configuration a node-entry spawn would. Only the resume-vs-mint decision above is
    node-entry-specific.

    **A re-spawn joins the pool.** A retry/adopt/reclaim/requeue spawn at a node whose
    ``session:`` names a declared session stamps that ``session_name`` exactly as
    ``fresh:<name>`` does, so it becomes the pool's head. Without it the retried attempt
    would be invisible to ``pool_head`` and a later ``resume:<name>`` would resume the
    **failed first attempt** — a regression against #115's ``resume:<node>``, which
    already returns the newest lease at that node."""
    if _spawn_suppressed(ctx, via=via, chunk_id=chunk_id):
        return
    now = ctx.clock.now()
    # Mint above the max of two floors (bzh:epoch-fencing, #112): the runner-local fence
    # source (`store.latest_epoch` — the highest epoch *this runner* minted for the chunk, 0
    # if it never drove it) and the **hub-supplied** floor `envelope.epoch` (the hub's own
    # `latest_epoch(facts)`, carried on every claim/advance response). Local alone is wrong for
    # the migration-reclaim path (#90): a cross-graph migration re-queues the chunk `ready` for
    # a fresh claim, possibly by a runner with no local history (floor 0) while the hub carries
    # prior-graph epochs > 0 — minting local+1 would land at or below hub truth and every
    # completion would bounce off the hub's stale-epoch fence. `max(local, hub) + 1` is >= the
    # old `local + 1` always, so existing non-migration fences are strengthened, never weakened.
    epoch = max(ctx.store.latest_epoch(chunk_id), envelope.epoch) + 1
    lease_id = mint(LEASE_PREFIX, ctx.clock)
    node = envelope.node
    retries_max = node.retries_max if node.retries_max is not None else ctx.config.default_retries_max
    resolved_model, resolved_effort = _resolve_model_and_effort(ctx, chunk_id, node, resume_from)
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
            session_name=node.session_name,
            resolved_model=resolved_model,
            resolved_effort=resolved_effort,
            created_at=now,
        )
    )
    # A per-lease capability token (issue #113, Phase 1): minted alongside the lease
    # itself, its hash stashed durably here, the plaintext carried forward only to
    # the spawn preamble (never persisted). Pure scaffold this phase — no caller yet
    # authorizes anything against `lease_token_hash`; a later attach endpoint is what
    # compares a presented token's hash against it.
    lease_token, token_hash = mint_lease_token()
    ctx.store.record_lease_token(lease_id, token_hash, now)
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
    # What standing prose the session being resumed was last sent (issue #149) — read here
    # and ONLY when this spawn resumes one, so a fresh spawn can never accidentally elide
    # (its session id is minted below and has nothing recorded against it yet). A resumed
    # session with nothing recorded — any session first spawned before #149 shipped — reads
    # `None` and renders in full, which is the safe direction and the whole back-compat
    # story. Do not hoist this above the `resume_from` check: a fresh spawn passes today
    # only because its session has no prior row *yet*, and that stops being true the moment
    # a session id is reused.
    # Truthiness, not `is not None`, to match the adapter's own `if resume_from:`
    # (`harness/internal/claude_code_adapter.py`): an empty string there falls through to
    # `--session-id`, i.e. a brand-new session. Under `is not None` the core would look up a
    # fingerprint for `""` and could elide — handing a session that has never seen the prose
    # a line saying its standing instructions are unchanged. Not reachable today (this
    # function always passes a uuid `session_hint`, and `latest_session_id` returns either a
    # real id or `None`), but the two predicates deciding "is this a resume?" differently is
    # the kind of divergence that only stays safe by accident.
    prior_preamble = ctx.store.session_preamble_fingerprint(resume_from) if resume_from else None
    rendered = render_worker_preamble(
        runner_prompt=ctx.config.runner_prompt,
        workspace_prompt=workspace_prompt,
        environments=environments,
        lease_id=lease_id,
        runner_id=ctx.config.runner_id,
        chunk_id=chunk_id,
        prior=prior_preamble,
    )
    prompt_prefix = rendered.text
    generation = _pending_generation(ctx, lease_id)
    preamble = WorkerPreamble(
        environments=environments,
        lease_id=lease_id,
        local_api_url=ctx.config.local_api_url,
        workspace_root=ctx.config.workspace_root,
        prompt_prefix=prompt_prefix,
        stdout_path=_stdout_path(ctx, lease_id, generation),
        stderr_path=_stderr_path(ctx, lease_id, generation),
        lease_token=lease_token,
    )
    try:
        handle = ctx.harness.spawn(
            envelope,
            preamble,
            session_hint=str(uuid.uuid4()),
            resume_from=resume_from,
            model=resolved_model,
            effort=resolved_effort,
        )
    except HarnessSpawnError as exc:
        # Surface the launch-time spawn failure (issue #125, change L(iii)) then RE-RAISE to
        # preserve today's propagation — no worker started, so the attempt has not been
        # recorded and the chunk simply retries next tick.
        _emit_command_failed(
            ctx,
            chunk_id=chunk_id,
            lease_id=lease_id,
            node_name=envelope.node.node_name,
            command="spawn harness worker",
            stderr_tail=str(exc),
        )
        raise
    ctx.store.record_spawn(
        lease_id,
        pid=handle.pid,
        process_start_time=handle.process_start_time,
        session_id=handle.session_id,
        spawned_at=now,
    )
    # What this session was just sent (issue #149), recorded on EVERY `_spawn_attempt` —
    # node entry, retry, adopt, reclaim, requeue-resume alike. A fresh spawn's record is the
    # baseline its first resume compares against, and the non-node-entry paths all mint
    # fresh sessions that receive the full prose anyway, so no conditional belongs here.
    # Keyed on the HANDLE's session id — the authoritative continuation id, which is
    # `resume_from` for an in-place resume and would be the fork id if the adapter ever
    # forked. Its own store call rather than a widening of `record_spawn`, whose three
    # resume-with-message call sites send no prompt_prefix at all: see
    # `record_session_preamble`'s docstring. Deliberately no crash-sweep point — the write
    # lands after the spawn returns, so a durable fingerprint always implies the prose
    # reached the process, and a kill that loses it leaves the next resume rendering in
    # full (pre-change behavior). Recorded as an exemption in
    # `blizzard-context:/architecture/crash-correctness.md`. Placed before the checkpoint
    # below so that point keeps meaning "every fact about this spawn is durable".
    ctx.store.record_session_preamble(handle.session_id, fingerprint=rendered.fingerprint, at=now)
    _CP_SPAWN_AFTER_SPAWN.reached()


#: The classification `_fail_attempt`'s branch chooses for its surfaced operational event
#: (issue #125): the retry branch is a `warning` (this attempt died, another will run), the
#: escalate branch a `critical` (retries exhausted — the worker is lost to a human), the
#: reassign-abandon branch an `info` (the attempt was given up because the chunk moved, not
#: a failure of the work). The locally-paused *defer* branch surfaces nothing — the failure
#: is deliberately deferred, not a surfaced outcome.
_ATTEMPT_FAILED = ("warning", "attempt-failed")
_WORKER_LOST = ("critical", "worker-lost")
_ATTEMPT_ABANDONED = ("info", "attempt-abandoned")


def _failure_event_payload(
    lease: LeaseRecord, *, severity: str, kind: str, message: str, reason: str, via: str, stderr_tail: str = ""
) -> str:
    """The ``event.recorded`` payload one `_fail_attempt` branch surfaces (issue #125).

    ``detail`` carries the ``(reason, via)`` that classified it and the node it happened
    at; a dead-worker case folds in the captured spawn-stderr tail (change L(iii)) when one
    was written. Kept a plain JSON string so it rides the outbound buffer exactly like every
    other fact payload."""
    detail: dict[str, object] = {"via": via, "reason": reason, "node": lease.node_name}
    if stderr_tail:
        detail["stderr_tail"] = stderr_tail
    return json.dumps(
        {
            "severity": severity,
            "kind": kind,
            "chunk_id": lease.chunk_id,
            "lease_id": lease.lease_id,
            "node_name": lease.node_name,
            "message": message,
            "detail": detail,
        }
    )


def _emit_command_failed(
    ctx: LoopContext,
    *,
    chunk_id: str | None,
    lease_id: str | None,
    node_name: str | None,
    command: str,
    stderr_tail: str,
) -> None:
    """Surface a captured spawn/verify/env-prep command failure as a ``warning``
    ``command-failed`` operational event (issue #125, change L).

    Enqueued straight to the outbound buffer — it rides no closure, and it never alters the
    caller's control flow: the spawn-launch site re-raises after calling this, the env-prep
    site returns False after, and the git-verify site (issue #143, Phase 4 — read-only, so
    no unsafe window to protect) simply drops the declaration and continues. The failing
    command and its stderr tail (already carried on the raised exception's message per
    MF-2) go in ``detail``."""
    payload = json.dumps(
        {
            "severity": "warning",
            "kind": "command-failed",
            "chunk_id": chunk_id,
            "lease_id": lease_id,
            "node_name": node_name,
            "message": f"command failed: {command}",
            "detail": {"command": command, "stderr_tail": stderr_tail[-2000:] if stderr_tail else ""},
        }
    )
    ctx.store.enqueue_outbound(
        kind=EVENT_RECORDED, chunk_id=chunk_id, lease_id=lease_id, payload=payload, created_at=ctx.clock.now()
    )


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

    # A dead worker (via ADVANCE) may have written a spawn-stderr tail (change L(iii));
    # fold it into every branch's event detail. Best-effort — absent/empty is the ordinary
    # case for a hung-but-live worker (REAP) that never crashed to stderr.
    stderr_tail = _stderr_tail(ctx, lease)

    # attempt_count includes this lease (its context row was written at mint); the
    # first attempt is not a retry, so retries-so-far is one less.
    retried = ctx.store.attempt_count(lease.chunk_id, lease.node_id) - 1
    if retried < lease.retries_max:
        # Retry: this attempt died, another will run — a `warning` `attempt-failed`,
        # enqueued ATOMICALLY with the closure it describes (issue #125, change K).
        severity, kind = _ATTEMPT_FAILED
        ctx.store.record_closure(
            lease_id=lease.lease_id,
            chunk_id=lease.chunk_id,
            node_id=lease.node_id,
            reason=reason,
            closed_at=now,
            event_kind=EVENT_RECORDED,
            event_payload=_failure_event_payload(
                lease,
                severity=severity,
                kind=kind,
                message=f"attempt failed, retrying — {reason} (via {via})",
                reason=reason,
                via=via,
                stderr_tail=stderr_tail,
            ),
        )
        _requeue(ctx, lease)
        return
    if _reassigned_or_detached(ctx, lease):
        # Reassign-abandon: the chunk moved on, so this attempt is given up — an `info`
        # `attempt-abandoned`. The closure lives inside `_abandon_reassigned`, which is
        # ALSO reached from RESUME/PULL's ordinary detach sweep (those must stay silent),
        # and `via` alone cannot tell a funnel-reached abandon from a plain detach — so the
        # event is emitted HERE, scoped to the `_fail_attempt` funnel, rather than inside
        # the shared helper (plan-findings SF-6). This one branch's enqueue is therefore not
        # co-transactional with its closure; it stays crash-safe for the same reason every
        # event does — informational, append-only, at-most-once-per-attempt structural, so a
        # `kill -9` between them at worst re-emits on the next attempt's failure.
        severity, kind = _ATTEMPT_ABANDONED
        ctx.store.enqueue_outbound(
            kind=EVENT_RECORDED,
            chunk_id=lease.chunk_id,
            lease_id=lease.lease_id,
            payload=_failure_event_payload(
                lease,
                severity=severity,
                kind=kind,
                message=f"attempt abandoned — chunk reassigned ({reason}, via {via})",
                reason=reason,
                via=via,
                stderr_tail=stderr_tail,
            ),
            created_at=now,
        )
        _abandon_reassigned(ctx, lease, killed=True, via=via)
        return
    if ctx.store.local_paused(ctx.config.runner_id):
        # Deliberate deferral, not a surfaced failure — emit nothing (issue #125).
        _log.info(
            "escalation deferred — locally paused",
            runner_id=ctx.config.runner_id,
            via=via,
            chunk_id=lease.chunk_id,
            lease_id=lease.lease_id,
        )
        return
    # Escalate: retries exhausted — the worker is lost to a human. A `critical`
    # `worker-lost`, enqueued ATOMICALLY with the closure it describes (issue #125).
    severity, kind = _WORKER_LOST
    ctx.store.record_closure(
        lease_id=lease.lease_id,
        chunk_id=lease.chunk_id,
        node_id=lease.node_id,
        reason=_ESCALATED,
        closed_at=now,
        event_kind=EVENT_RECORDED,
        event_payload=_failure_event_payload(
            lease,
            severity=severity,
            kind=kind,
            message=f"worker lost — retries exhausted ({reason}, via {via})",
            reason=reason,
            via=via,
            stderr_tail=stderr_tail,
        ),
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
    (an open escalation with no later lease mint). It carries two takeover
    strings: the wrapped, supported entry point — ``blizzard runner takeover
    <chunk_id> --dir <runner_dir>`` (issue #251) — a human runs to have the
    blizzard runner itself resume the parked session, and the raw pasteable
    fallback — ``cd <workdir> && <harness resume>`` composed from the adapter's
    session surface — for when the wrapped verb is unavailable. A requeue's
    later lease mint closes the escalation by supersession. Environments stay
    bound throughout.

    ``reason`` is log-line prose only — every caller's escalation (retries-exhausted,
    :func:`_park_on_cost_cap`'s spend cap) rides the identical wire fact and takeover
    composition; only why it happened differs.
    """
    now = ctx.clock.now()
    bindings = ctx.store.bindings_for_chunk(lease.chunk_id)
    takeover = ""
    wrapped_takeover = ""
    if lease.session_id is not None and bindings:
        # Composed from the lease's own stamps (issue #144), so the operator who takes
        # this escalation over lands in exactly the configuration the parked session ran
        # with — never a fresh resolution that could flip a live session's model.
        takeover = ctx.harness.resume_command(
            bindings[0].workdir,
            lease.session_id,
            model=lease.resolved_model,
            effort=lease.resolved_effort,
        )
        # The wrapped command's presence tracks the raw one in lockstep (both empty, or
        # both set) — under the same session+bindings guard, gated further on a resolved
        # runtime dir so an unresolved one composes nothing rather than a broken command.
        if ctx.config.runner_dir:
            wrapped_takeover = f"blizzard runner takeover {lease.chunk_id} --dir {shlex.quote(ctx.config.runner_dir)}"
    payload = json.dumps(
        {
            "chunk_id": lease.chunk_id,
            "epoch": lease.epoch,
            "takeover_command": takeover,
            "wrapped_takeover_command": wrapped_takeover,
            "route_token": ctx.store.route_token(lease.chunk_id),  # issue #84a
        }
    )
    ctx.store.enqueue_outbound(
        kind=ESCALATION_RECORDED, chunk_id=lease.chunk_id, lease_id=lease.lease_id, payload=payload, created_at=now
    )
    _log.info(
        f"escalated to needs-human — {reason}", chunk_id=lease.chunk_id, takeover=takeover, wrapped=wrapped_takeover
    )


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
        # Reasserted, not sticky (issue #144) — see the judge call site's note.
        effort=lease.resolved_effort,
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
        # Reasserted, not sticky (issue #144) — see the judge call site's note.
        effort=lease.resolved_effort,
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
) -> list[ProducesEntry]:
    """Every `produces:` spec this attempt does not yet cover (issue #143, D2) — the
    nudge-worthy set :func:`_advance_exited_worker` checks before submitting. Order
    follows the envelope's own `produces:` declaration, not attachment/git order, so the
    nudge message and a node's declared list read in the same sequence. Mirrors
    :func:`_collect_asset_artifacts`'s own git-coverage check rather than sharing it:
    the two run at different points in the same attempt (this one before the nudge,
    that one after), over ``attachments`` snapshots that may legitimately differ.

    Returns the unmet specs themselves, not just their names (issue #143, Phase 5) —
    :func:`_nudge_message` needs each spec's `kind` to name the kind-appropriate
    declaration verb, not just the deprecated single-verb nudge issue #113 shipped.

    ``attachments`` has not yet been folded into a ``SubmittedArtifact`` list at this
    point in the attempt (that assembly is :func:`_collect_asset_artifacts`'s job,
    which runs after the nudge), so this synthesizes an ``attached=True`` artifact per
    attachment purely to hand the shared predicate one artifact list to evaluate — the
    same shape :func:`_collect_asset_artifacts` will itself submit.

    Evaluated by :func:`~blizzard.wire.completion.produces_coverage`, the same shared
    predicate the hub's own backstop checks
    (:func:`~blizzard.hub.domain.produces_auth.check_produces`) — an ``asset`` spec by
    name, a ``git_commit`` spec by kind (any ``GIT_COMMIT`` artifact present) — so the
    two coverage models cannot drift apart again."""

    attached = [
        SubmittedArtifact(name=name, kind=ArtifactKind.ASSET, content=content, attached=True)
        for name, content in attachments.items()
    ]
    return produces_coverage(envelope.node.produces, git_artifacts + attached)


def _nudge_message(missing: list[ProducesEntry]) -> str:
    """The nudge resume's message (issue #113, Phase 4; kind-branched issue #143, Phase
    5): one `#`-prefixed comment line per unmet `produces:` spec, naming the
    kind-appropriate declaration verb and its correct positionals — an `asset` spec
    names `artifact create --name <name>` (content on stdin); a `git_commit` spec
    names `artifact commit --repo/--branch/--commit` (the worker's own values, per
    repo it touched — this nudge cannot supply them; `--forge` is omitted here since
    it defaults to the repo's own `origin`). Mirrors
    :data:`_PAUSE_RESUME_MESSAGE`'s shape, so the mock harness's prompt-is-program exec
    sees a legal no-op script while a real harness reads the same text as an ordinary
    resume instruction."""

    lines = ["# This node's `produces:` still needs an explicit submission:"]
    for spec in missing:
        if spec.kind is ArtifactKind.GIT_COMMIT:
            lines.append(
                f"#   - {spec.name} (git_commit): push your branch, then run "
                f"`blizzard runner artifact commit --repo <repo> --branch <branch> "
                f"--commit <sha>` for each repo you touched (`<repo>` is its name in "
                f"the environment's manifest; add `--env <id>` when the chunk holds "
                f"more than one environment)."
            )
        else:
            lines.append(
                f"#   - {spec.name} (asset): run `blizzard runner artifact create "
                f"--name {spec.name}` with the content on stdin."
            )
    lines.append("# Do this before this attempt is judged done.")
    return "\n".join(lines)


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

    covered = {a.name for a in git_artifacts}
    submitted: list[SubmittedArtifact] = []
    for spec in envelope.node.produces:
        if spec.kind is ArtifactKind.GIT_COMMIT:
            continue
        name = spec.name
        if name in covered:
            continue
        if name in attachments:
            submitted.append(
                SubmittedArtifact(name=name, kind=ArtifactKind.ASSET, content=attachments[name], attached=True)
            )
        else:
            submitted.append(SubmittedArtifact(name=name, kind=ArtifactKind.ASSET, content=assessment))
    return submitted


def _verify_and_collect_git_commits(
    ctx: LoopContext,
    lease: LeaseRecord,
    bindings: list[EnvBindingRecord],
    already_declared: dict[tuple[str, str], GitCommitDeclarationRecord] | None = None,
) -> tuple[list[SubmittedArtifact], dict[tuple[str, str], GitCommitDeclarationRecord]]:
    """Read back the worker's declared git commits for this lease and confirm each,
    read-only, against the origin the declaring environment's repo manifest names
    (issue #143, Phase 4) — replaces the runner's former infer-and-push. The worker has
    already pushed its branch and declared ``(env, repo, branch, commit)`` through the
    local declaration channel (Phase 3, `blizzard runner artifact commit`); this never
    mutates git and never infers a branch name off residue.

    Spans **every** bound environment, not just the first. A chunk holding several envs
    has a worktree of the same repo in each, and the declaration key carries the env, so
    each is collected on its own terms; reading only ``bindings[0]`` would drop envs
    2..N's work with no error, which is the same silent-loss shape this whole seam exists
    to remove.

    A declaration that does not verify is **not** silently dropped. It is reported via
    :func:`_emit_command_failed` naming the declared branch/commit against the origin it
    was checked at, so the worker — which is still alive and still holds the context —
    can correct it, and it still counts as "not covered" for the Phase-2 kind-coverage
    nudge (:func:`_missing_produces`). Relying on non-coverage alone was load-bearing
    exactly once: when the coverage check could not see the ``git_commit`` spec, nothing
    was left to notice, and a chunk sailed to `done` having delivered nothing. A verify
    subprocess failure (unreachable origin, a network hiccup) is reported the same way —
    informational only: a read-only re-derivation opens no unsafe mutation window, so
    unlike the push it replaces, this never re-raises to crash-loop the tick.

    ``already_declared`` names the exact declarations this lease's attempt already
    resolved on an earlier call this attempt (the pre-nudge pass) — an ``(env, repo)``
    whose freshly re-derived declaration is unchanged is skipped entirely (no re-verify,
    no re-emit), since re-verifying it would only repeat the same outcome (a duplicate
    `command-failed` the worker never acted on). One whose declaration differs (the
    worker re-declared it mid-nudge) is a genuine amendment and is re-verified fresh.
    Returns the collected artifacts alongside this call's own full declaration set, so a
    caller can thread it into a later call as that later call's ``already_declared``."""

    already_declared = already_declared or {}
    declarations = ctx.store.git_commit_declarations_for_lease(lease.lease_id)
    origins = _repo_origins(ctx, bindings)
    submitted: list[SubmittedArtifact] = []
    for key, declared in declarations.items():
        if already_declared.get(key) == declared:
            continue
        env_id, repo = key
        origin_url = origins.get(key)
        if origin_url is None:
            # The declare edge checks the repo against the env's manifest, so reaching
            # here means the manifest changed under the lease (an env re-prepared
            # mid-attempt) rather than a worker typo. Report it: an unresolvable origin
            # means this commit cannot be delivered, and that is worth saying out loud.
            _emit_command_failed(
                ctx,
                chunk_id=lease.chunk_id,
                lease_id=lease.lease_id,
                node_name=lease.node_name,
                command=f"resolve origin for --repo {repo!r} in environment {env_id!r}",
                stderr_tail=(
                    f"environment {env_id!r} no longer lists repo {repo!r}; "
                    f"it lists {sorted(name for (env, name) in origins if env == env_id)}"
                ),
            )
            continue
        try:
            verified = ctx.worktree_git.verify(origin_url, declared.branch, declared.commit)
        except WorktreeGitError as exc:
            _emit_command_failed(
                ctx,
                chunk_id=lease.chunk_id,
                lease_id=lease.lease_id,
                node_name=lease.node_name,
                command=f"git ls-remote {origin_url} {declared.branch} (--repo {repo!r}, --env {env_id!r})",
                stderr_tail=str(exc),
            )
            continue
        if not verified:
            _emit_command_failed(
                ctx,
                chunk_id=lease.chunk_id,
                lease_id=lease.lease_id,
                node_name=lease.node_name,
                command=f"git ls-remote {origin_url} {declared.branch} (--repo {repo!r}, --env {env_id!r})",
                stderr_tail=(
                    f"declared commit {declared.commit} is not what branch {declared.branch!r} "
                    f"points at on {origin_url} — push the branch (or re-declare the sha "
                    f"`git rev-parse HEAD` actually produced) and declare it again"
                ),
            )
            continue
        submitted.append(
            SubmittedArtifact(
                name=repo,
                kind=ArtifactKind.GIT_COMMIT,
                forge=origin_url,
                repo=repo,
                branch_name=declared.branch,
                commit_hash=declared.commit,
            )
        )
    return submitted, declarations


def _repo_origins(ctx: LoopContext, bindings: list[EnvBindingRecord]) -> dict[tuple[str, str], str]:
    """``{(environment_id, repo): origin_url}`` across every bound environment.

    The provider is the authority on both which repos an env holds and where each one
    pushes, so this is a lookup rather than a derivation — blizzard never joins an env
    workdir to a repo name to guess a path, and never reads ``origin`` from whatever
    directory it happens to be standing in."""
    origins: dict[tuple[str, str], str] = {}
    for binding in bindings:
        for repo in ctx.provider.repos(binding.environment_id):
            origins[(binding.environment_id, repo.name)] = repo.origin_url
    return origins


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


def _checks_block(results: list[CheckResultRecord]) -> str:
    """The runner-executed check results injected into the judgement prompt (issue #114).

    Rendered as ``#``-prefixed lines for the same harness-agnostic reason
    :func:`_elicitation_tail` is (a mock harness ``exec``s the prompt as a script; a
    bare-prose block would be a ``SyntaxError``). One line per check with its command and
    ``PASS``/``FAIL``, so the worker judges against mechanical truth rather than its own
    recollection. A failed check additionally shows its captured output tail (the durable
    runner-local evidence, [MF3]) indented below — where the worker needs the *why*; a
    passing check needs none. Empty for a node with no checks — then a no-op that adds
    nothing to the prompt (the empty-checks case AC #6 rests on).
    """
    if not results:
        return ""
    lines = ["", "", "# Checks (runner-executed at your exit — judge against these, not your recollection):"]
    for r in results:
        lines.append(f"#   [{'PASS' if r.passed else 'FAIL'}] {r.command}")
        if not r.passed:
            for tail_line in r.output_tail.strip().splitlines():
                lines.append(f"#       {tail_line}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# EXTERNAL SUBSCRIPTION USAGE SAMPLE (issue #218)
# --------------------------------------------------------------------------- #


def _external_usage_payload(snapshot: ExternalSubscriptionUsageSnapshot) -> dict[str, object]:
    """The stable JSON shape for a sampled snapshot — both this attempt's stored
    ``payload`` and its buffered outbound report use this exact shape, and phase 3's wire
    fact payload is defined to match it field-for-field: ``sampled_at``, ``windows``, and
    per-window ``window``/``utilization_pct``/``resets_at``/``window_seconds``."""
    return {
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


def sample_external_subscription_usage(ctx: LoopContext) -> None:
    """Sample the harness's own subscription rate-limit utilization (issue #218) — the
    tick's last step, run **after** ADVANCE (:func:`blizzard.runner.loop.tick.tick`, behind
    CEILING, REAP, RESUME, PULL, FILL and ADVANCE), the mirror image of
    :func:`check_spend_ceiling`'s reasoning for running first: that check gates every later
    step's spawn/kill decisions within the same pass, so it must run before them; this step
    gates nothing — no other step in this tick or any later one reads what it wrote before
    deciding anything — so there is no correctness reason to run it earlier, and every
    reason not to: its only network call (the harness's own usage endpoint, via
    :meth:`~blizzard.runner.harness.adapter.IHarnessAdapter.sample_external_subscription_usage`)
    must never sit ahead of REAP's stale-worker reap or FILL's claim-and-spawn in the same
    pass and delay either on a diagnostic read neither depends on.

    **Cadence gate.** Reads :meth:`~blizzard.runner.store.repository.
    IReadRunnerStore.last_external_usage_attempt_at` — the derived ``max(sampled_at)``
    anchor, never a separately-stored "last sampled" column. ``None`` (never attempted)
    samples immediately; otherwise this attempt is skipped unless
    ``ctx.clock.now() - anchor`` has reached ``ctx.config.external_usage_sample_interval_
    seconds`` (at exactly the interval, it samples — not strictly past it).

    **On a sample**, records the attempt row keyed on ``ctx.clock.now()`` — not the
    snapshot's own ``sampled_at`` — so the cadence anchor stays on *this* runner's own
    timeline regardless of what clock the adapter samples against (``bzh:injected-clock``);
    the snapshot's own ``sampled_at`` still rides the JSON payload's ``sampled_at`` field
    (what the harness actually reported), just not the cadence-driving column. Enqueues the
    outbound report under the shared ``wire/facts.py`` :data:`~blizzard.wire.facts.
    EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED` kind. **On no sample** (``None`` — no subscription
    concept, or this attempt produced nothing), records the attempt with a ``NULL``
    payload and buffers no report; the next tick's cadence gate reads the attempt back
    regardless, so a harness with nothing to report is not re-queried every tick.

    Wrapped in a broad ``except Exception`` as a **second line of defense**: the adapter
    contract already promises never to raise (:meth:`~blizzard.runner.harness.adapter.
    IHarnessAdapter.sample_external_subscription_usage`'s docstring), but this is a
    diagnostic sample bolted onto the tick's very last step, and the tick's "always
    completes" invariant must not depend on every adapter conforming to that promise —
    an adapter that raises anyway must still leave REAP/RESUME/PULL/FILL/ADVANCE's work
    from *this same tick* intact rather than losing it to an exception that unwinds past
    ``tick()``.

    Crash safety: the only durable write here is the single-transaction
    ``record_external_usage_attempt`` (attempt fact + its optional hub-bound report,
    atomic by construction — the same call shape ``record_local_pause``/``record_usage``
    use, which carry no crash point of their own for the same reason). The read before it
    (``last_external_usage_attempt_at``) and the adapter call are side-effect-free from
    the store's perspective, so a crash at any point up to the write leaves nothing to
    recover: the next tick simply re-derives the identical cadence decision from the same
    durable attempts and the (now later) clock. This opens no new crash window, so no new
    crash-point-registry point is added.
    """
    try:
        anchor = ctx.store.last_external_usage_attempt_at()
        if anchor is not None:
            elapsed = ctx.clock.now() - anchor
            if elapsed < timedelta(seconds=ctx.config.external_usage_sample_interval_seconds):
                return
        snapshot = ctx.harness.sample_external_subscription_usage()
        if snapshot is None:
            ctx.store.record_external_usage_attempt(
                sampled_at=ctx.clock.now(), payload=None, report_kind="", report_payload=""
            )
            return
        payload = json.dumps(_external_usage_payload(snapshot))
        ctx.store.record_external_usage_attempt(
            sampled_at=ctx.clock.now(),
            payload=payload,
            report_kind=EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED,
            report_payload=payload,
        )
    except Exception as exc:  # second line of defense — the adapter contract already promises this
        _log.warning("external subscription usage sample step failed", detail=str(exc))
