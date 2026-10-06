"""Judging an exited worker's node-step: checks, verdict, produces, completion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from blizzard.foundation.crash import crashpoint
from blizzard.foundation.leases import LeaseClosureReason
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import iso_utc
from blizzard.foundation.trace_ids import StepKey, step_root
from blizzard.runner.environments.repository import EnvBinding
from blizzard.runner.harness.adapter import IHarnessLifecycleAndVerdict
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.registry import UnavailableHarnessError, UnknownHarnessError
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.hub.client import HubClientError
from blizzard.runner.hub.outbound import OutboundFacts
from blizzard.runner.leases import Lease
from blizzard.runner.leases.elicitation import PendingElicitation
from blizzard.runner.lifecycle.attempt import Attempt
from blizzard.runner.lifecycle.dormant import DormantContext, DormantSession
from blizzard.runner.lifecycle.judgement.checks import CheckPlan, ExecutedCheck
from blizzard.runner.lifecycle.judgement.collect import CollectOutcome, ElicitationExit
from blizzard.runner.lifecycle.judgement.exit_route import (
    ExitEntry,
    ExitNotJudgeable,
    ExitRoute,
    JudgeableExit,
    route_exit,
)
from blizzard.runner.lifecycle.judgement.git_commits import DeclaredCommits
from blizzard.runner.lifecycle.judgement.judgement_prompt import JudgementPrompt
from blizzard.runner.lifecycle.judgement.produces import ProducesReconciler
from blizzard.runner.lifecycle.judgement.verdict import Verdict, VerdictOutcome
from blizzard.runner.lifecycle.spawn import Spawner
from blizzard.runner.lifecycle.usage_limit import classify_judge_usage_limit, engage_and_park_judge
from blizzard.runner.node_steps.envelope import Envelope, ProducesSpec
from blizzard.runner.node_steps.submissions import CheckVerdict, Completion, CompletionArtifact, GateSubmission
from blizzard.runner.throttle.overload import classify_judge_overload, record_judge_overload, reset_if_streak_open

_log = get_logger("blizzard.runner.loop")

# Checks-at-exit: result rows are durable before the marker, so a crash between
# them leaves `checks_ran` unset and recovery safely re-runs (latest-wins).
_CP_CHECKS_AFTER_RESULTS = crashpoint(
    "checks.after-results.before-marker",
    "check result rows durable; the checks-ran marker has not been written yet",
)
_CP_CHECKS_AFTER_MARKER = crashpoint(
    "checks.after-marker.before-judge",
    "checks-ran marker durable; the judgement has not been elicited yet",
)

# Verify -> elicit verdict -> buffer completion. Verify is read-only, so it needs no crash
# point of its own (`bzh:crash-correctness` exemption). Usage recording sits
# between the verdict and the buffer: a crash there finds this attempt's usage facts already
# durable, or neither — never a double-count.
_CP_AFTER_JUDGE = crashpoint("advance.after-judgement.before-buffer", "verdict parsed; completion not buffered")
_CP_AFTER_USAGE = crashpoint("advance.after-usage.before-buffer", "usage facts recorded; completion not buffered")

# Elicit LAUNCH — mint-before-spawn's own shape: the in-flight record is
# durable BEFORE the process starts, so an orphaned Popen can never happen; only an
# un-armable record-with-no-process gap exists, absorbed the same way SPAWN's is. Reached on
# every ordinary judgement, so `advance.*` is the honest family.
_CP_ELICIT_AFTER_RECORD = crashpoint(
    "advance.after-elicit-record.before-launch", "in-flight elicitation record durable; the process has not launched"
)
_CP_ELICIT_AFTER_LAUNCH = crashpoint("advance.after-elicit-launch", "elicitation launched; pid recorded")

# Resume-once: the durable `(lease, epoch)` fact is recorded BEFORE the
# resume it guards, so "at most one resume" holds across a crash at either point.
_CP_NUDGE_AFTER_FIRED_FACT = crashpoint(
    "nudge.after-fired-fact.before-resume",
    "resume-fired fact durable; the resume that wakes the session has not run yet",
)

_CP_AFTER_BUFFER = crashpoint("advance.after-buffer.before-flush", "completion buffered; not yet flushed")


class JudgementContext(DormantContext, Protocol): ...


def _elicitation_alive(ctx: JudgementContext, elicitation: PendingElicitation) -> bool:
    pid, start_time = elicitation.pid, elicitation.process_start_time or ""
    return pid is not None and ctx.process.is_alive(pid, start_time)


def elicitation_still_pending(ctx: JudgementContext, elicitation: PendingElicitation) -> bool:
    """Whether `Judgement.collect` would only wait on this elicitation — live and under the bound —
    without paying for a `Judgement` (the envelope fetch and binding read `Judgement.of` performs).
    Stale-and-alive and exited both return ``False``. Reads the same rule `collect` does
    (`PendingElicitation.pending`), so the two never diverge on thresholds."""
    return elicitation.pending(ctx.clock.now(), alive=_elicitation_alive(ctx, elicitation))


@dataclass(frozen=True)
class Judgement:
    """One exited worker's node-step, judged — its declared commits confirmed, its ``checks:``
    run, a verdict elicited from the dead session, and the completion buffered."""

    ctx: JudgementContext
    lease: Lease
    envelope: Envelope
    bindings: list[EnvBinding]

    @property
    def session(self) -> SessionReference:
        """The session this exit's verdict is elicited from — present on every lease
        :meth:`of` admits."""
        return JudgeableExit.of(self.lease).session

    @classmethod
    def of(cls, ctx: JudgementContext, lease: Lease) -> Judgement | None:
        """This exit's judgement, or ``None`` when there is nothing to judge this tick — a
        lease with no recorded session (left to REAP), no bound environment, or a hub that
        could not hand over the envelope to judge against."""
        try:
            JudgeableExit.of(lease)
        except ExitNotJudgeable:
            _log.warning("exited worker with no recorded session — not judging", lease_id=lease.lease_id)
            return None
        bindings = ctx.stores.environments.bindings_for_chunk(lease.chunk_id)
        if not bindings:
            _log.warning("exited worker with no bound env — skipping", chunk_id=lease.chunk_id)
            return None
        try:
            with ctx.tracer.under(step_root(StepKey.attempt(lease.chunk_id, lease.epoch))):
                envelope = ctx.hub.get_envelope(lease.chunk_id)
        except HubClientError:
            return None  # hub unreachable — the worker's exit is durable; retry next tick
        return cls(ctx, lease, envelope, bindings)

    def run(self) -> None:
        """Confirm the commits, then buffer a human's decision, resume a premature exit, or
        launch the verdict elicitation — the produces reconcile sits below the human gate and
        the local spawn brake alike, since `DormantSession.resume_on_unmet_produces` is itself
        a spawn.

        Launching is this method's final act: the elicitation is detached, so
        no path through here waits on a model turn — a later reconciliation pass collects the
        verdict via :meth:`collect`."""
        self._enter(ExitEntry.EXIT)

    def resume(self) -> None:
        """Re-enter judgement after a usage-limit park or a provider-overload backoff: the
        exit already passed the gate and the produces nudge before its first elicitation, so
        this goes straight to a fresh launch, under the local spawn brake alone."""
        self._enter(ExitEntry.JUDGE_RESUME)

    def _enter(self, entry: ExitEntry) -> None:
        lease = self.lease
        gated = lease.node_name in self.ctx.config.gates
        route = route_exit(entry, gated=gated)
        artifacts: list[CompletionArtifact] = []
        if route is not ExitRoute.ELICIT:
            # Confirmed ahead of the gate and the brake alike: a buffered decision and the
            # produces reconcile both read them.
            artifacts = DeclaredCommits(self.ctx, lease, self.bindings).verify()
        if route is ExitRoute.BUFFER_DECISION:
            # This operator gates this node by name, so the outcome is a human's: buffer a
            # Decision instead of eliciting a verdict. Not a spawn, so it is ungated.
            self._buffer_decision(artifacts)
            return
        if Spawner(self.ctx).suppressed(via="advance", chunk_id=lease.chunk_id, lease_id=lease.lease_id):
            return
        if route is ExitRoute.RECONCILE_PRODUCES:
            produces = ProducesReconciler(self.envelope)
            # Names only — this check never reads content; `_judged` below
            # still fetches the full `attachments_for_lease` where content is genuinely needed.
            attached_names = self.ctx.stores.attachments.attachment_names_for_lease(lease.lease_id)
            missing = produces.missing(artifacts, attached_names)
            # The nudge fact is loaded only when there is something to nudge about.
            spent = bool(missing) and self.ctx.stores.checks.nudge_fired(lease.lease_id, lease.epoch)
            route = route_exit(entry, gated=gated, produces_unmet=bool(missing), nudge_spent=spent)
            if route is ExitRoute.NUDGE:
                self._nudge(produces, missing)
                return
        self._launch()

    def _nudge(self, produces: ProducesReconciler, missing: list[ProducesSpec]) -> None:
        """Resume-once: an exit with `produces:` unmet is resumed, not judged — no verdict
        elicited, no attempt failed, and no `checks:` run."""
        lease = self.lease
        _log.warning(
            "resuming premature exit for unattached produces names",
            node=self.envelope.node.node_name,
            missing=[spec.name for spec in missing],
            lease_id=lease.lease_id,
            epoch=lease.epoch,
        )
        self.ctx.stores.checks.record_nudge_fired(lease_id=lease.lease_id, epoch=lease.epoch, at=self.ctx.clock.now())
        # The nudge's own boundary, riding `record_nudge_fired`'s own
        # pre-resume transaction — no new window, so no new crash point brackets it.
        start_position, start_unreadable = self.ctx.resolve_boundary_start(self.session, self.bindings[0].workdir)
        self.ctx.stores.invocation_boundaries.record_boundary_open(
            lease_id=lease.lease_id,
            chunk_id=lease.chunk_id,
            node_id=lease.node_id,
            epoch=lease.epoch,
            generation=Spawner(self.ctx).generation(lease.lease_id),
            kind="nudge",
            start_position=start_position,
            start_unreadable=start_unreadable,
            opened_at=self.ctx.clock.now(),
        )
        _CP_NUDGE_AFTER_FIRED_FACT.reached()
        DormantSession(self.ctx, lease).resume_on_unmet_produces(produces.nudge_message(missing), self.bindings)

    def collect(self, elicitation: PendingElicitation) -> None:
        """Poll this lease's in-flight elicitation; once its process has exited, read its
        reply back and continue exactly where a launch's own reply would have.

        A still-live process is bounded by staleness alone — a hung process that never exits
        must still fail, not wait forever. An **exited** process is classified for a usage
        limit ahead of the staleness bound: an elicitation observed only
        long after it exited — a delayed tick, a runner outage — must still pause rather than
        fail, exactly the shape the staleness bound would otherwise catch first.
        Exited, not limited, and nothing usable at all — empty, or a partial write with no
        result envelope at all, the shape a `kill -9` mid-write leaves — is a **lost**
        elicitation, not a verdict-less reply: that relaunches under the staleness bound
        rather than consuming a retry.

        The record is cleared, and its output files swept, only AFTER the collected reply
        is fully processed: a crash mid-processing leaves the record standing,
        so the next pass re-reads the same still-present file and re-runs `_judged` — safe
        because usage recording and completion buffering are already idempotent replays
        under a crash, the same guarantee the once-synchronous elicitation always leaned on."""
        lease = self.lease
        observed = ElicitationExit(elicitation, self.ctx.clock.now(), alive=_elicitation_alive(self.ctx, elicitation))
        outcome = observed.decide()
        if outcome is CollectOutcome.WAIT:
            return
        if outcome is CollectOutcome.FAIL_STALE:
            # `Attempt.fail` kills the (possibly still-running) process and clears this
            # record itself — no separate write of our own precedes it.
            self._fail_stale(elicitation)
            return
        output = self.ctx.elicitation_files.read(elicitation.output_path)
        # Classified ahead of both the staleness bound and the lost-output check: a usage-limited harness
        # typically writes its own signal to the session transcript, not this elicitation's captured stdout,
        # so neither an empty `output` nor a long-unobserved exit may fall through to failing or relaunching —
        # exactly what a usage-limit pause exists to avoid.
        generation = self.ctx.stores.liveness.lease_generation(lease.lease_id)
        lines = self.ctx.usage.judge_transcript_lines(lease, self.bindings, generation=generation)
        limit = classify_judge_usage_limit(self.ctx, lease, output, lines)
        backing_off = False
        if limit is None:
            # A provider-overloaded elicitation is classified right alongside the usage
            # limit — the two are mutually exclusive exit reasons for the
            # one exit, both read from the same `output`/`lines` pair read once above,
            # same as the worker's own classification order in steps.py. A streak at its
            # limit is not backing off, and falls through to the ordinary path below.
            overload = classify_judge_overload(self.ctx, lease, output, lines)
            if overload is not None:
                identity = iso_utc(elicitation.first_launched_at)
                backing_off = record_judge_overload(
                    self.ctx, lease, overload, generation=generation, invocation_identity=identity
                )
            else:
                reset_if_streak_open(self.ctx, lease)
        usage_limited, output_present = limit is not None, bool(output)
        outcome = observed.decide(
            usage_limited=usage_limited, overload_backing_off=backing_off, output_present=output_present
        )
        if outcome is CollectOutcome.READ_HARNESS:
            harness = self._resolve_harness(self.session, via="collect")
            if harness is None:
                # `_resolve_harness` already escalated — unlike a lost write, no other runner can
                # resume this exact session, so relaunching would only stall a chunk not coming back.
                return
            outcome = observed.decide(
                usage_limited=usage_limited,
                overload_backing_off=backing_off,
                output_present=output_present,
                usable=harness.has_usable_output(output),
            )
        if outcome is CollectOutcome.PARK and limit is not None:
            engage_and_park_judge(self.ctx, lease, limit)
        elif outcome is CollectOutcome.FAIL_STALE:
            self._fail_stale(elicitation)
        elif outcome is CollectOutcome.RELAUNCH:
            self._lost(elicitation)
        elif outcome is CollectOutcome.JUDGE:
            self._judged(output)
            self.ctx.stores.elicitations.clear_elicitation(lease.lease_id, lease.epoch)
            self.ctx.elicitation_files.cleanup(lease.lease_id, lease.epoch, through_attempt=elicitation.relaunch_count)
        # BACK_OFF: backing off in place — the next tick's `backing_off_facts` picks it up.

    def _fail_stale(self, elicitation: PendingElicitation) -> None:
        lease = self.lease
        _log.warning(
            "elicitation past its staleness bound — failing attempt",
            chunk_id=lease.chunk_id,
            lease_id=lease.lease_id,
            relaunch_count=elicitation.relaunch_count,
        )
        Attempt(self.ctx, lease).fail(reason=LeaseClosureReason.FAILED, via="advance")

    def _lost(self, elicitation: PendingElicitation) -> None:
        """The elicitation's process exited without writing anything usable. Relaunch —
        `collect` has already checked staleness unconditionally above, so reaching here means
        this attempt is still under the bound.

        The local-pause brake gates the relaunch exactly as it gates a fresh launch: a paused
        runner defers rather than spawning, the record untouched, mirroring
        `Reap`'s own "a pause is not a drain" treatment of a stale worker — the bound is
        re-checked against a fresh `now` on the first pass after the brake clears, so a long
        pause does not by itself cause an immediate abandon, but does not buy the attempt
        extra time past the bound either."""
        lease = self.lease
        if Spawner(self.ctx).suppressed(via="advance", chunk_id=lease.chunk_id, lease_id=lease.lease_id):
            return
        _log.warning(
            "elicitation lost — relaunching, no retry consumed",
            chunk_id=lease.chunk_id,
            lease_id=lease.lease_id,
            relaunch_count=elicitation.relaunch_count,
        )
        self._relaunch(elicitation)

    def _relaunch(self, elicitation: PendingElicitation) -> None:
        """Re-launch a lost elicitation into a fresh output file (never a second document
        appended to the lost attempt's own file) and record-before-launch as the first launch
        does — the narrow gap between the two is a self-healing accepted loss (no window
        entry: a restart mid-relaunch reads the still-unset pid as not-running and relaunches
        again), not a `bzh:crash-point-registry` window."""
        lease = self.lease
        output_path = self.ctx.elicitation_files.output_path(
            lease.lease_id, lease.epoch, attempt=elicitation.next_attempt
        )
        self.ctx.stores.elicitations.record_elicitation_relaunch(lease.lease_id, lease.epoch, output_path=output_path)
        self._elicit(output_path)

    def checks(self) -> list[ExecutedCheck]:
        """Run the node's ``checks:`` at worker exit, or read the results back.

        Rows are recorded before the marker, which is what makes them exactly-once across a
        crash. The re-run key is ``(lease, epoch)``, so a retry re-runs against the rebuilt tree."""
        node = self.envelope.node
        lease = self.lease
        plan = CheckPlan.of(node, self.bindings[0].workdir)
        if not plan.commands:
            return []
        if self.ctx.stores.checks.checks_ran(lease.lease_id, lease.epoch):
            return self.ctx.stores.checks.check_results_for_lease(lease.lease_id, lease.epoch)
        if self.ctx.check_runner is None:
            # The seam is unwired but the node declares checks — a wiring bug, never a
            # production path. Surface it loudly and skip rather than wedge the tick.
            _log.error(
                "node declares checks but no check-runner seam is wired — skipping checks",
                node=node.node_name,
                lease_id=lease.lease_id,
            )
            return []
        results: list[ExecutedCheck] = []
        for command in plan.commands:
            outcome = self.ctx.check_runner.run(command, plan.cwd, plan.timeout)
            results.append(ExecutedCheck(command=command, passed=outcome.passed, output_tail=outcome.output_tail))
        # Rows first, then the marker — what `runner:checks-recorded-when-marked` rests on.
        self.ctx.stores.checks.record_check_results(
            lease_id=lease.lease_id,
            chunk_id=lease.chunk_id,
            node_id=lease.node_id,
            epoch=lease.epoch,
            results=results,
            at=self.ctx.clock.now(),
        )
        _CP_CHECKS_AFTER_RESULTS.reached()
        self.ctx.stores.checks.record_checks_ran(lease_id=lease.lease_id, epoch=lease.epoch, at=self.ctx.clock.now())
        _CP_CHECKS_AFTER_MARKER.reached()
        _log.info(
            "checks executed",
            node=node.node_name,
            count=len(results),
            red=sum(1 for r in results if not r.passed),
            lease_id=lease.lease_id,
        )
        return results

    def _launch(self) -> None:
        """Launch the detached verdict elicitation and return — a re-minted lease identity, since the
        worker is gone. Checks run first, against the tree the worker just left. The in-flight record is
        durable BEFORE the process starts (`Spawner.spawn`'s mint-before-spawn), so a crash in the gap
        leaves a record REAP's staleness absorbs. A launch over a standing record restarts the bound and
        sweeps the prior record's output files first."""
        lease = self.lease
        standing = self.ctx.stores.elicitations.in_flight_elicitation(lease.lease_id, lease.epoch)
        if standing is not None:
            self.ctx.elicitation_files.cleanup(lease.lease_id, lease.epoch, through_attempt=standing.relaunch_count)
        output_path = self.ctx.elicitation_files.output_path(lease.lease_id, lease.epoch, attempt=0)
        self.ctx.stores.elicitations.record_elicitation_launch(
            lease.lease_id, lease.epoch, output_path=output_path, at=self.ctx.clock.now()
        )
        # The judgement's own boundary, riding `record_elicitation_launch`'s own
        # pre-launch write. Keyed by the CURRENT generation — a judgement mints no new one.
        start_position, start_unreadable = self.ctx.resolve_boundary_start(self.session, self.bindings[0].workdir)
        self.ctx.stores.invocation_boundaries.record_boundary_open(
            lease_id=lease.lease_id,
            chunk_id=lease.chunk_id,
            node_id=lease.node_id,
            epoch=lease.epoch,
            generation=self.ctx.stores.liveness.lease_generation(lease.lease_id),
            kind="judge",
            start_position=start_position,
            start_unreadable=start_unreadable,
            opened_at=self.ctx.clock.now(),
        )
        _CP_ELICIT_AFTER_RECORD.reached()
        self._elicit(output_path)
        _CP_ELICIT_AFTER_LAUNCH.reached()

    def _elicit(self, output_path: str) -> None:
        """Render the judgement prompt against this attempt's own checks and launch it into
        ``output_path`` — the shared half of a fresh launch and a lost
        answer's relaunch, including reasserting the stamped effort/compaction-
        window the same way on both: neither is session-sticky, so
        a resume that omits them drops the declared value back to the ambient default."""
        lease = self.lease
        checks = self.checks()
        message = JudgementPrompt(self.envelope, checks).render()
        session = self.session
        harness = self._resolve_harness(session, via="elicit")
        if harness is None:
            # `_resolve_harness` already escalated, clearing the in-flight record as part of
            # that closure — no other runner can resume this exact session to relaunch on.
            return
        handle = harness.judge(
            SpawnCwd.of_session(self.ctx.config.workspace_root, self.bindings[0].workdir),
            session.session_id,
            message,
            output_path,
            preamble=Spawner(self.ctx).preamble(lease, self.bindings),
            chunk_id=lease.chunk_id,
            effort=lease.resolved_effort,
            model=lease.resolved_model,
            compaction_window=lease.resolved_compaction_window,
        )
        try:
            self.ctx.stores.elicitations.record_elicitation_started(
                lease.lease_id,
                lease.epoch,
                pid=handle.pid,
                process_start_time=handle.process_start_time,
                pgid=handle.pgid,
            )
        except Exception:
            # A plain raise never disarms the trampoline on its own — kill it explicitly, as `Spawner.spawn`
            # does; an `ElicitationNotRecorded` leaves nothing to track the judge by, so it is reaped too.
            self.ctx.process.kill_group(handle.pgid)
            raise
        # Disarm only now this record is durable — `collect` can re-adopt it past here.
        handle.confirm_durable()

    def _judged(self, output: str) -> None:
        """Continue from a collected reply — usage, verdict, the checks gate, the completion —
        in the same order the once-synchronous elicitation left them in. Reached only
        from :meth:`collect`, which already resolved this exact session's owner as its own
        guard, so the resolution below can never be reached with an unresolvable one."""
        lease = self.lease
        # Record this attempt's harness usage *before* the verdict is parsed, so a
        # verdict-less fail does not discard the spend the attempt genuinely burned.
        self.ctx.usage.record_attempt(lease, self.bindings, judge_output=output)

        harness = self.ctx.harnesses.lifecycle_and_verdict(self.session.harness_id)
        verdict = Verdict.of(harness.parse_verdict(output), self.envelope.node.choices)
        selected = verdict.selected
        if selected is None:
            # Ask-during-judgement: the worker escalated instead of returning a verdict. The
            # pre-elicitation check in `_advance_exited_worker` cannot see this one — it was
            # recorded during the elicitation just above — so park on it here instead of
            # burning a retry on a verdict that was never coming. A choice the node does not
            # declare is verdict-less the same way, never buffered for the hub to refuse.
            ask = self.ctx.stores.asks.unforwarded_ask(lease.lease_id)
            if verdict.without_choice(ask) is VerdictOutcome.PARK_ON_ASK and ask is not None:
                DormantSession(self.ctx, lease).park_on_ask(ask)
                return
            _log.warning(
                "verdict-less judgement — failing attempt",
                chunk_id=lease.chunk_id,
                lease_id=lease.lease_id,
                choice=verdict.choice,
            )
            Attempt(self.ctx, lease).fail(reason=LeaseClosureReason.FAILED, via="advance")
            return
        _CP_AFTER_JUDGE.reached()
        _CP_AFTER_USAGE.reached()
        # The checks gate judges the exact checks the worker was shown — gate and worker can
        # never diverge on "the tree".
        checks = self.checks()
        if verdict.gated(checks) is VerdictOutcome.FAIL_RED_CHECKS:
            _log.warning(
                "requires_checks choice selected with a red check — failing attempt",
                chunk_id=lease.chunk_id,
                lease_id=lease.lease_id,
                choice=selected.name,
            )
            Attempt(self.ctx, lease).fail(reason=LeaseClosureReason.FAILED, via="advance")
            return

        # Harvest asset artifacts for any `produces` name no git commit covers, read from the
        # durable store so a restart between attach and completion still sees it.
        produces = ProducesReconciler(self.envelope)
        artifacts = DeclaredCommits(self.ctx, lease, self.bindings).verify()
        assessment = harness.parse_assessment(output)
        attachments = self.ctx.stores.attachments.attachments_for_lease(lease.lease_id)
        artifacts += produces.collect_assets(artifacts, assessment, attachments)
        self._buffer_completion(selected.name, checks, artifacts)

    def _buffer_decision(self, artifacts: list[CompletionArtifact]) -> None:
        """Buffer a runner-config gate decision — the gated node-step's outcome.

        The choice set is not the runner's, so the submission carries only the step's artifacts
        and its fence; ADVANCE skips this lease until the flush closes it."""
        lease = self.lease
        submission = GateSubmission(
            from_node_id=lease.node_id,
            epoch=lease.epoch,
            artifacts=artifacts,
            route_token=self.ctx.stores.tokens.route_token(lease.chunk_id),
            lease_id=lease.lease_id,
        )
        OutboundFacts(self.ctx).decision(lease, submission, at=self.ctx.clock.now())
        _log.info("runner-config gate: decision buffered", chunk_id=lease.chunk_id, node=lease.node_name)

    def _buffer_completion(self, choice: str, checks: list[ExecutedCheck], artifacts: list[CompletionArtifact]) -> None:
        """One atomic, epoch-fenced write. The entry names the lease, so ADVANCE skips it
        until the flush closes it."""
        lease = self.lease
        submission = Completion(
            choice=choice,
            epoch=lease.epoch,
            from_node_id=lease.node_id,
            # `(command, passed)` only — `output_tail` stays runner-local, off the wire.
            check_results=[CheckVerdict(command=r.command, passed=r.passed) for r in checks],
            artifacts=artifacts,
            route_token=self.ctx.stores.tokens.route_token(lease.chunk_id),
            lease_id=lease.lease_id,
        )
        OutboundFacts(self.ctx).completion(lease, submission, at=self.ctx.clock.now())
        _CP_AFTER_BUFFER.reached()
        _log.info("completion buffered", chunk_id=lease.chunk_id, lease_id=lease.lease_id, choice=choice)

    def _resolve_harness(self, session: SessionReference, *, via: str) -> IHarnessLifecycleAndVerdict | None:
        """Resolve this judgement's recorded owner; escalate the chunk in place via
        :meth:`Attempt.escalate_owner_unresolvable` and return ``None`` — never raising —
        when it is unknown or unavailable."""
        try:
            return self.ctx.harnesses.lifecycle_and_verdict(session.harness_id)
        except (UnknownHarnessError, UnavailableHarnessError) as exc:
            Attempt(self.ctx, self.lease).escalate_owner_unresolvable(session=session, exc=exc, via=via)
            return None
