"""Judging an exited worker's node-step: checks, verdict, produces, completion."""

from __future__ import annotations

import os
from dataclasses import dataclass

from blizzard.foundation.crash import crashpoint
from blizzard.foundation.logging import get_logger
from blizzard.runner.loop.attempt import FAILED, Attempt
from blizzard.runner.loop.checks import DEFAULT_CHECK_TIMEOUT
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.dormant import DormantSession
from blizzard.runner.loop.git_commits import DeclaredCommits
from blizzard.runner.loop.hub import HubClientError
from blizzard.runner.loop.judgement_prompt import JudgementPrompt
from blizzard.runner.loop.outbound import OutboundFacts
from blizzard.runner.loop.produces import ProducesReconciler
from blizzard.runner.loop.spawn import Spawner
from blizzard.runner.store.repository import CheckResultRecord, EnvBindingRecord, LeaseRecord
from blizzard.wire.completion import CheckResult, ChecksGate, CompletionSubmission, SubmittedArtifact
from blizzard.wire.decision import DecisionSubmission
from blizzard.wire.envelope import NodeEnvelope

_log = get_logger("blizzard.runner.loop")

# Checks-at-exit (issue #114): result rows are durable before the marker, so a crash between
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
# point of its own (`bzh:crash-correctness` exemption). Usage recording (issue #58) sits
# between the verdict and the buffer: a crash there finds this attempt's usage facts already
# durable, or neither — never a double-count.
_CP_AFTER_JUDGE = crashpoint("advance.after-judgement.before-buffer", "verdict parsed; completion not buffered")
_CP_AFTER_USAGE = crashpoint("advance.after-usage.before-buffer", "usage facts recorded; completion not buffered")

# Nudge-once (issue #113): the durable `(lease, epoch)` fact is recorded BEFORE the resume it
# guards, so "at most one nudge" holds across a crash at either point.
_CP_NUDGE_AFTER_FIRED_FACT = crashpoint(
    "nudge.after-fired-fact.before-resume",
    "nudge-fired fact durable; the resume that delivers the nudge has not run yet",
)
_CP_NUDGE_AFTER_RESUME = crashpoint(
    "nudge.after-resume.before-reassemble",
    "nudge resume returned; attachments not yet re-read and the completion not yet reassembled",
)

_CP_AFTER_BUFFER = crashpoint("advance.after-buffer.before-flush", "completion buffered; not yet flushed")


@dataclass(frozen=True)
class Judgement:
    """One exited worker's node-step, judged — its declared commits confirmed, its ``checks:``
    run, a verdict elicited from the dead session, and the completion buffered."""

    ctx: LoopContext
    lease: LeaseRecord
    envelope: NodeEnvelope
    bindings: list[EnvBindingRecord]

    @classmethod
    def of(cls, ctx: LoopContext, lease: LeaseRecord) -> Judgement | None:
        """This exit's judgement, or ``None`` when there is nothing to judge this tick — no
        bound environment, or a hub that could not hand over the envelope to judge against."""
        bindings = ctx.store.bindings_for_chunk(lease.chunk_id)
        if not bindings:
            _log.warning("exited worker with no bound env — skipping", chunk_id=lease.chunk_id)
            return None
        try:
            envelope = ctx.hub.get_envelope(lease.chunk_id)
        except HubClientError:
            return None  # hub unreachable — the worker's exit is durable; retry next tick
        return cls(ctx, lease, envelope, bindings)

    def run(self) -> None:
        """Confirm the commits, then either buffer a human's decision or elicit the verdict.

        The elicitation is itself a spawn, so the local brake gates it (issue #45) — placed
        below the confirm, since nothing above it starts a process."""
        lease = self.lease
        commits = DeclaredCommits(self.ctx, lease, self.bindings)
        artifacts = commits.verify()
        if lease.node_name in self.ctx.config.gates:
            # This operator gates this node by name, so the outcome is a human's: buffer a
            # Decision instead of eliciting a verdict. Not a spawn, so it is ungated.
            self._buffer_decision(artifacts)
            return
        if Spawner(self.ctx).suppressed(via="advance", chunk_id=lease.chunk_id, lease_id=lease.lease_id):
            return

        # Checks run before the judgement (issue #114), against the tree the worker just left
        # — the same tree its judgement and the gate are rendered on.
        checks = self.checks()
        output = self._elicit(JudgementPrompt(self.envelope, checks).render())
        # Record this attempt's harness usage (issue #58) *before* the verdict is parsed, so a
        # verdict-less fail does not discard the spend the attempt genuinely burned.
        self.ctx.usage.record_attempt(lease, self.bindings, judge_output=output)

        choice = self.ctx.harness.parse_verdict(output)
        if choice is None:
            # Ask-during-judgement: the worker escalated instead of returning a verdict. The
            # pre-elicitation check in `_advance_exited_worker` cannot see this one — it was
            # recorded during the elicitation just above — so park on it here instead of
            # burning a retry on a verdict that was never coming.
            ask = self.ctx.store.unforwarded_ask(lease.lease_id)
            if ask is not None:
                DormantSession(self.ctx, lease).park_on_ask(ask)
                return
            _log.warning("verdict-less judgement — failing attempt", chunk_id=lease.chunk_id, lease_id=lease.lease_id)
            Attempt(self.ctx, lease).fail(reason=FAILED, via="advance")
            return
        _CP_AFTER_JUDGE.reached()
        _CP_AFTER_USAGE.reached()
        if self._gate_broken(choice, checks):
            Attempt(self.ctx, lease).fail(reason=FAILED, via="advance")
            return

        assessment = self.ctx.harness.parse_assessment(output)
        attachments = self.ctx.store.attachments_for_lease(lease.lease_id)
        produces = ProducesReconciler(self.envelope)
        missing = produces.missing(artifacts, attachments)
        if missing and not self.ctx.store.nudge_fired(lease.lease_id, lease.epoch):
            # Nudge-once (issue #113): the guard fact is recorded BEFORE the resume, which is
            # what makes "at most one nudge per (lease, epoch)" hold across a kill -9 at either.
            _log.warning(
                "nudging worker for unattached produces names",
                node=self.envelope.node.node_name,
                missing=[spec.name for spec in missing],
                lease_id=lease.lease_id,
                epoch=lease.epoch,
            )
            self.ctx.store.record_nudge_fired(lease_id=lease.lease_id, epoch=lease.epoch, at=self.ctx.clock.now())
            _CP_NUDGE_AFTER_FIRED_FACT.reached()
            # `judge`, not `resume_with_message`: the reply is discarded, but the resume must be
            # *synchronous* or the attachments re-read below races the worker still attaching.
            nudge_output = self._elicit(produces.nudge_message(missing))
            _CP_NUDGE_AFTER_RESUME.reached()
            self._record_nudge_usage(nudge_output)
            # Re-read: a worker that attached during the nudge must have its content picked up
            # before assembly below, not the assessment fallback it just corrected.
            attachments = self.ctx.store.attachments_for_lease(lease.lease_id)
            artifacts = commits.amend(artifacts)

        # Harvest asset artifacts for any `produces` name no git commit covers, read from the
        # durable store so a restart between attach and completion still sees it.
        artifacts += produces.collect_assets(artifacts, assessment, attachments)
        self._buffer_completion(choice, checks, artifacts)

    def checks(self) -> list[CheckResultRecord]:
        """Run the node's ``checks:`` at worker exit, or read the results back (issue #114).

        Rows are recorded before the marker, which is what makes them exactly-once across a
        crash. The re-run key is ``(lease, epoch)``, so a retry re-runs against the rebuilt tree."""
        node = self.envelope.node
        lease = self.lease
        if not node.checks:
            return []
        if self.ctx.store.checks_ran(lease.lease_id, lease.epoch):
            return self.ctx.store.check_results_for_lease(lease.lease_id, lease.epoch)
        if self.ctx.check_runner is None:
            # The seam is unwired but the node declares checks — a wiring bug, never a
            # production path. Surface it loudly and skip rather than wedge the tick.
            _log.error(
                "node declares checks but no check-runner seam is wired — skipping checks",
                node=node.node_name,
                lease_id=lease.lease_id,
            )
            return []
        workdir = self.bindings[0].workdir
        cwd = os.path.join(workdir, node.checks_cwd) if node.checks_cwd else workdir
        timeout = node.checks_timeout or DEFAULT_CHECK_TIMEOUT
        results: list[CheckResultRecord] = []
        for command in node.checks:
            outcome = self.ctx.check_runner.run(command, cwd, timeout)
            results.append(CheckResultRecord(command=command, passed=outcome.passed, output_tail=outcome.output_tail))
        # Rows first, then the marker — what `runner:checks-recorded-when-marked` rests on.
        self.ctx.store.record_check_results(
            lease_id=lease.lease_id,
            chunk_id=lease.chunk_id,
            node_id=lease.node_id,
            epoch=lease.epoch,
            results=results,
            at=self.ctx.clock.now(),
        )
        _CP_CHECKS_AFTER_RESULTS.reached()
        self.ctx.store.record_checks_ran(lease_id=lease.lease_id, epoch=lease.epoch, at=self.ctx.clock.now())
        _CP_CHECKS_AFTER_MARKER.reached()
        _log.info(
            "checks executed",
            node=node.node_name,
            count=len(results),
            red=sum(1 for r in results if not r.passed),
            lease_id=lease.lease_id,
        )
        return results

    def _elicit(self, message: str) -> str:
        """One synchronous turn in the dead worker's session, carrying a re-minted lease
        identity — the worker is gone, so invalidating its token orphans nothing."""
        return self.ctx.harness.judge(
            self.bindings[0].workdir,
            self.lease.session_id or "",
            message,
            preamble=Spawner(self.ctx).preamble(self.lease, self.bindings),
            chunk_id=self.lease.chunk_id,
            # Reassert the stamped effort (issue #144): effort is NOT session-sticky, so a
            # resume that omits it drops the declared value back to the ambient default.
            effort=self.lease.resolved_effort,
            model=self.lease.resolved_model,
            # Reassert the stamped compaction window (blizzard#343) too — not session-sticky
            # either, mirroring effort's treatment.
            compaction_window=self.lease.resolved_compaction_window,
        )

    def _gate_broken(self, choice: str, checks: list[CheckResultRecord]) -> bool:
        """The checks gate (issue #114), evaluated BEFORE the nudge so it judges the exact
        checks the worker was shown — gate and worker can never diverge on "the tree"."""
        selected = next((c for c in self.envelope.node.choices if c.name == choice), None)
        if selected is None or not ChecksGate(selected.requires_checks, checks).violated:
            return False
        _log.warning(
            "requires_checks choice selected with a red check — failing attempt",
            chunk_id=self.lease.chunk_id,
            lease_id=self.lease.lease_id,
            choice=choice,
        )
        return True

    def _record_nudge_usage(self, output: str) -> None:
        """A distinct ``nudge`` kind (issue #58) so it cannot collide with the primary
        judgement's own fact at this same generation."""
        generation = self.ctx.store.lease_generation(self.lease.lease_id)
        sample = self.ctx.harness.parse_usage(output, "nudge", model=self.lease.resolved_model)
        if sample is not None:
            self.ctx.usage.record_sample(self.lease, generation=generation, sample=sample)

    def _buffer_decision(self, artifacts: list[SubmittedArtifact]) -> None:
        """Buffer a runner-config gate decision — the gated node-step's outcome.

        The choice set is not the runner's, so the submission carries only the step's artifacts
        and its fence; ADVANCE skips this lease until the flush closes it."""
        lease = self.lease
        submission = DecisionSubmission(
            from_node_id=lease.node_id,
            epoch=lease.epoch,
            runner_id=self.ctx.config.runner_id,
            artifacts=artifacts,
            route_token=self.ctx.store.route_token(lease.chunk_id),
        )
        OutboundFacts(self.ctx).decision(lease, submission, at=self.ctx.clock.now())
        _log.info("runner-config gate: decision buffered", chunk_id=lease.chunk_id, node=lease.node_name)

    def _buffer_completion(
        self, choice: str, checks: list[CheckResultRecord], artifacts: list[SubmittedArtifact]
    ) -> None:
        """One atomic, epoch-fenced write. The entry names the lease, so ADVANCE skips it
        until the flush closes it."""
        lease = self.lease
        submission = CompletionSubmission(
            choice=choice,
            epoch=lease.epoch,
            runner_id=self.ctx.config.runner_id,
            from_node_id=lease.node_id,
            # `(command, passed)` only — `output_tail` stays runner-local, off the wire.
            check_results=[CheckResult(command=r.command, passed=r.passed) for r in checks],
            artifacts=artifacts,
            route_token=self.ctx.store.route_token(lease.chunk_id),
        )
        OutboundFacts(self.ctx).completion(lease, submission, at=self.ctx.clock.now())
        _CP_AFTER_BUFFER.reached()
        _log.info("completion buffered", chunk_id=lease.chunk_id, lease_id=lease.lease_id, choice=choice)
