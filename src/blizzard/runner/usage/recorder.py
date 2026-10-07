"""One attempt's harness token usage, parsed off its own output and made durable."""

from __future__ import annotations

from dataclasses import dataclass, replace

from blizzard.foundation.clock import IClock
from blizzard.foundation.fact_kinds import USAGE_RECORDED
from blizzard.foundation.logging import get_logger
from blizzard.runner.environments.repository import EnvBinding
from blizzard.runner.events.publisher import IRunnerEventPublisher
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.registry import IHarnessRegistry, UnavailableHarnessError, UnknownHarnessError
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.harness.transcript import TranscriptPosition
from blizzard.runner.harness.usage import UsageKind, UsageSample
from blizzard.runner.leases.liveness import IReadLeaseLivenessRepository
from blizzard.runner.leases.model import Lease
from blizzard.runner.leases.worker_stdout import IWorkerStdoutReader
from blizzard.runner.transcripts.invocation_boundaries import (
    WORKER_STARTING_KINDS,
    InvocationBoundary,
    InvocationBoundaryKind,
    InvocationBoundaryStart,
    IReadInvocationBoundaryRepository,
)
from blizzard.runner.usage.repository import (
    IWriteUsageRepository,
    charge_range,
    derive_invocation_cost,
    effective_model,
    usage_kind_for,
)

_log = get_logger("blizzard.runner.loop")


@dataclass(frozen=True)
class UsageRecorder:
    """Records a lease's usage facts, keyed ``(lease, generation, kind)`` so they are
    idempotent across a re-run and a crash finds each durable or absent."""

    leases: IReadLeaseLivenessRepository
    usage: IWriteUsageRepository
    clock: IClock
    worker_files: IWorkerStdoutReader
    workspace_root: str
    #: Required; every recorded session's owner resolves through this registry, with no single-harness fallback.
    harnesses: IHarnessRegistry
    #: The generation's own boundary — the range-read fallback's start.
    invocation_boundaries: IReadInvocationBoundaryRepository
    #: The transcripts lane's on/off switch — ``False`` disables only the envelope-less usage fallback.
    transcripts_wired: bool = False
    #: The SSE publish seam, typed against the Protocol
    #: (``bzh:dependency-inversion``); ``None`` on a loop-only caller, a no-op there.
    events: IRunnerEventPublisher | None = None

    def record_worker(self, lease: Lease, bindings: list[EnvBinding]) -> None:
        """Record just this attempt's spawn/resume invocation usage — no judgement ran."""
        generation = self.leases.lease_generation(lease.lease_id)
        sample = self._worker_sample(lease, bindings, generation=generation, kind=usage_kind_for(generation))
        if sample is not None:
            self.record_sample(lease, generation=generation, sample=sample)

    def record_attempt(self, lease: Lease, bindings: list[EnvBinding], *, judge_output: str) -> None:
        """Record the spawn/resume invocation ADVANCE is judging and the judgement resume
        that elicited its verdict — each its own fact."""
        self.record_worker(lease, bindings)
        generation = self.leases.lease_generation(lease.lease_id)
        session = lease.session
        if session is None:
            return
        harness = self.harnesses.usage_accounting(session.harness_id)
        requested = lease.resolved_model
        needs_transcript = self.transcripts_wired and harness.needs_usage_transcript(judge_output, model=requested)
        lines = self.judge_transcript_lines(lease, bindings, generation=generation) if needs_transcript else []
        # The judge's own transcript range is read for what actually ran only when the lease asked for nothing.
        model = effective_model(requested, harness.observed_model(lines) if requested is None and lines else None)
        judge_start = self.invocation_boundaries.current_start(lease.lease_id, generation, "judge")
        judge_sample = harness.parse_usage(
            judge_output,
            "judge",
            model=model,
            transcript_lines=lines,
            invocation_start=judge_start.at if judge_start is not None else None,
            invocation_end=self.clock.now(),
        )
        if judge_sample is not None:
            self.record_sample(lease, generation=generation, sample=judge_sample)

    def record_sample(self, lease: Lease, *, generation: int, sample: UsageSample) -> None:
        """Make one already-parsed sample durable against this lease's generation, stamped
        with the lease's own recorded harness identity — never a fresh
        resolution that may since have changed."""
        session = lease.session
        if session is not None:
            sample = replace(
                sample,
                harness_id=session.harness_id,
                harness_version=self.leases.latest_spawn_harness_version(lease.lease_id),
            )
        cost = derive_invocation_cost(sample, self.usage.session_cost_basis(lease.lease_id))
        seq = self.usage.record_usage(
            lease_id=lease.lease_id,
            chunk_id=lease.chunk_id,
            node_id=lease.node_id,
            epoch=lease.epoch,
            generation=generation,
            sample=sample,
            cost=cost,
            recorded_at=self.clock.now(),
        )
        # `None` on an exact-replay idempotent no-op (`record_usage`'s own docstring) — nothing
        # was enqueued, so nothing to announce, and no rejected reading to warn of a second time.
        if seq is not None and cost.billed_reading_rejected:
            # Absent cost here is a rejected reading, not a worker that died before its
            # envelope — the two are indistinguishable on the board, so say so once here.
            _log.warning(
                "harness cost figure reads below what its session already banked",
                lease_id=lease.lease_id,
                chunk_id=lease.chunk_id,
                generation=generation,
                reported_cost_usd=sample.cost_usd,
            )
        if seq is not None and self.events is not None:
            self.events.publish_fact_changed(
                seq=seq,
                kind=USAGE_RECORDED,
                chunk_id=lease.chunk_id,
                lease_id=lease.lease_id,
            )

    def _worker_sample(
        self, lease: Lease, bindings: list[EnvBinding], *, generation: int, kind: UsageKind
    ) -> UsageSample | None:
        """This attempt's own spawn/resume usage, parsed off *this generation's own* stdout
        envelope, falling back to a transcript-summed, cost-absent sample when none survived.
        Never fabricated: no envelope and no transcript is simply no fact."""
        output = self.worker_files.read_stdout(lease.lease_id, generation)
        session = lease.session
        if session is None:
            return None
        harness = self.harnesses.usage_accounting(session.harness_id)
        requested = lease.resolved_model
        needs_transcript = (
            self.transcripts_wired and bool(output) and harness.needs_usage_transcript(output, model=requested)
        )
        lines = self.worker_transcript_lines(lease, bindings, generation=generation) if needs_transcript else []
        # Observed before `parse_usage`, which prices a model-less stdout envelope only off this model.
        observed = harness.observed_model(lines) if requested is None and lines else None
        model = effective_model(requested, observed)
        boundary = self._worker_boundary(lease.lease_id, generation)
        judge = self.invocation_boundaries.boundary(lease.lease_id, generation, "judge")
        start = boundary.opened_at if boundary is not None else None
        end = min(self.clock.now(), judge.opened_at) if judge is not None else self.clock.now()
        sample = (
            harness.parse_usage(
                output, kind, model=model, transcript_lines=lines, invocation_start=start, invocation_end=end
            )
            if output
            else None
        )
        if sample is not None:
            return sample
        if not lines:
            # A fresh read: nothing was observed of these lines, so the adapter derives it.
            lines = self.worker_transcript_lines(lease, bindings, generation=generation)
            observed = None
        if not lines:
            return None
        return harness.sum_transcript_usage(
            lines, kind, model=model, invocation_start=start, invocation_end=end, observed=observed
        )

    def worker_transcript_lines(self, lease: Lease, bindings: list[EnvBinding], *, generation: int) -> list[str]:
        """This generation's own worker-starting-to-judge-or-tail transcript range,
        raw — the read half of :meth:`_worker_sample`'s own fallback, extracted
        so a usage-limit classification reads the identical range a usage sum would sum."""
        session = lease.session
        if session is None or not self.transcripts_wired:
            return []
        return self._read_range(
            lease.lease_id,
            session,
            bindings,
            generation=generation,
            start=self._worker_boundary(lease.lease_id, generation),
            end_kind="judge",
        )

    def judge_transcript_lines(self, lease: Lease, bindings: list[EnvBinding], *, generation: int) -> list[str]:
        """This generation's own judge-boundary-to-tail transcript range, raw —
        the judge's own turns, read the same way :meth:`worker_transcript_lines` reads the
        worker's; there is no boundary after a judge's own within one generation, so the
        end is always the tail."""
        session = lease.session
        if session is None or not self.transcripts_wired:
            return []
        return self._read_range(
            lease.lease_id,
            session,
            bindings,
            generation=generation,
            start=self.invocation_boundaries.current_start(lease.lease_id, generation, "judge"),
            end_kind=None,
        )

    def _read_range(
        self,
        lease_id: str,
        session: SessionReference,
        bindings: list[EnvBinding],
        *,
        generation: int,
        start: InvocationBoundary | InvocationBoundaryStart | None,
        end_kind: InvocationBoundaryKind | None,
    ) -> list[str]:
        """The raw lines :func:`charge_range` charges this invocation for — a same-generation
        later invocation's recorded start (``end_kind``) caps the read."""
        end_boundary = (
            self.invocation_boundaries.boundary(lease_id, generation, end_kind) if end_kind is not None else None
        )
        charged = charge_range(start, end_boundary)
        if charged is None:
            return []
        fallback_workdir = bindings[0].workdir if bindings else None
        spawn_cwd = SpawnCwd(self.workspace_root, fallback_workdir).path
        try:
            source = self.harnesses.transcript_source(session.harness_id)
        except (UnknownHarnessError, UnavailableHarnessError) as exc:
            # No transcript source registered for this owner: no fallback read, never a
            # raise out of a usage-recording or usage-limit-classifying call site.
            _log.info(
                "transcript range read blocked by unavailable harness transcript source",
                harness_id=session.harness_id,
                detail=str(exc),
            )
            return []
        end = (
            TranscriptPosition(charged.end)
            if charged.end is not None
            else source.tail_position(session.session_id, spawn_cwd=spawn_cwd)
        )
        start_position = TranscriptPosition(charged.start) if charged.start is not None else None
        return source.read_raw_lines(session.session_id, spawn_cwd=spawn_cwd, start=start_position, end=end)

    def _worker_boundary(self, lease_id: str, generation: int) -> InvocationBoundary | None:
        """This generation's own worker-starting boundary — whichever of spawn/resume/nudge
        actually opened it, since `record_worker`'s usage-kind label doesn't reliably name it."""
        for kind in WORKER_STARTING_KINDS:
            boundary = self.invocation_boundaries.boundary(lease_id, generation, kind)
            if boundary is not None:
                return boundary
        return None
