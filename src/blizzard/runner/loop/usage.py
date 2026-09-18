"""One attempt's harness token usage, parsed off its own output and made durable."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.runner.domain.invocation_boundaries import (
    InvocationBoundaryKind,
    InvocationBoundaryRecord,
    IReadInvocationBoundaryRepository,
)
from blizzard.runner.domain.leases import IReadLeaseLivenessRepository, LeaseRecord
from blizzard.runner.domain.usage import IWriteUsageRepository
from blizzard.runner.environments.repository import EnvBindingRecord
from blizzard.runner.events.publisher import IRunnerEventPublisher
from blizzard.runner.harness.adapter import IHarnessUsageAccounting
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.registry import IHarnessRegistry, UnavailableHarnessError, UnknownHarnessError
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.harness.transcript import TranscriptPosition
from blizzard.runner.harness.usage import UsageKind, UsageSample
from blizzard.runner.loop.worker_stdout import WorkerStdoutFiles
from blizzard.wire.facts import USAGE_RECORDED

_log = get_logger("blizzard.runner.loop")

#: Worker-starting boundary kinds, tried in order; `"judge"` excluded — it can coexist (blizzard#437).
_WORKER_STARTING_KINDS: tuple[InvocationBoundaryKind, ...] = ("spawn", "resume", "nudge")


@dataclass(frozen=True)
class UsageRecorder:
    """Records a lease's usage facts, keyed ``(lease, generation, kind)`` so they are
    idempotent across a re-run and a crash finds each durable or absent."""

    leases: IReadLeaseLivenessRepository
    usage: IWriteUsageRepository
    clock: IClock
    worker_files: WorkerStdoutFiles
    workspace_root: str
    #: Required; every recorded session's owner resolves through this registry, with no single-harness fallback.
    harnesses: IHarnessRegistry
    #: The generation's own boundary (blizzard#437) — the range-read fallback's start.
    invocation_boundaries: IReadInvocationBoundaryRepository
    #: The transcripts lane's on/off switch — ``False`` disables only the envelope-less usage fallback.
    transcripts_wired: bool = False
    #: The SSE publish seam (D2, blizzard#317), typed against the Protocol
    #: (``bzh:dependency-inversion``); ``None`` on a loop-only caller, a no-op there.
    events: IRunnerEventPublisher | None = None

    def record_worker(self, lease: LeaseRecord, bindings: list[EnvBindingRecord]) -> None:
        """Record just this attempt's spawn/resume invocation usage — no judgement ran."""
        generation = self.leases.lease_generation(lease.lease_id)
        kind: UsageKind = "spawn" if generation <= 1 else "resume"
        sample = self._worker_sample(lease, bindings, generation=generation, kind=kind)
        if sample is not None:
            self.record_sample(lease, generation=generation, sample=sample)

    def record_attempt(self, lease: LeaseRecord, bindings: list[EnvBindingRecord], *, judge_output: str) -> None:
        """Record the spawn/resume invocation ADVANCE is judging and the judgement resume
        that elicited its verdict — each its own fact."""
        self.record_worker(lease, bindings)
        generation = self.leases.lease_generation(lease.lease_id)
        # Attribute to the lease's own `resolved_model` stamp (issue #144), not the adapter
        # default: a judge turn on a sonnet session would otherwise book its spend against opus.
        session = lease.session
        if session is None:
            return
        judge_sample = self._resolved_harness(session).parse_usage(judge_output, "judge", model=lease.resolved_model)
        if judge_sample is not None:
            self.record_sample(lease, generation=generation, sample=judge_sample)

    def record_sample(self, lease: LeaseRecord, *, generation: int, sample: UsageSample) -> None:
        """Make one already-parsed sample durable against this lease's generation."""
        seq = self.usage.record_usage(
            lease_id=lease.lease_id,
            chunk_id=lease.chunk_id,
            node_id=lease.node_id,
            epoch=lease.epoch,
            generation=generation,
            sample=sample,
            recorded_at=self.clock.now(),
        )
        # `None` on an exact-replay idempotent no-op (`record_usage`'s own docstring) — nothing
        # was enqueued, so nothing to announce.
        if seq is not None and self.events is not None:
            self.events.publish_fact_changed(
                seq=seq,
                kind=USAGE_RECORDED,
                chunk_id=lease.chunk_id,
                lease_id=lease.lease_id,
            )

    def _worker_sample(
        self, lease: LeaseRecord, bindings: list[EnvBindingRecord], *, generation: int, kind: UsageKind
    ) -> UsageSample | None:
        """This attempt's own spawn/resume usage, parsed off *this generation's own* stdout
        envelope, falling back to a transcript-summed, cost-absent sample when none survived.
        Never fabricated: no envelope and no transcript is simply no fact."""
        output = self.worker_files.read_stdout(lease.lease_id, generation)
        session = lease.session
        if session is None:
            return None
        # Same attribution fallback as the judge fact (issue #144): on a resume the stamp is
        # what the session was MINTED with, not what a fresh resolution would produce now.
        harness = self._resolved_harness(session)
        sample = harness.parse_usage(output, kind, model=lease.resolved_model) if output else None
        if sample is not None:
            return sample
        if not self.transcripts_wired:
            return None
        boundary = self._worker_boundary(lease.lease_id, generation)
        if boundary is None:
            # No durable start for this exact generation: never charge the whole session to
            # one generation (blizzard#437 Phase 4) — no boundary, no sample.
            return None
        fallback_workdir = bindings[0].workdir if bindings else None
        spawn_cwd = SpawnCwd(self.workspace_root, fallback_workdir).path
        try:
            source = self.harnesses.transcript_source(session.harness_id)
        except (UnknownHarnessError, UnavailableHarnessError) as exc:
            # No transcript source registered for this owner: no fallback sample, never a
            # raise out of a usage-recording call site.
            _log.info(
                "usage transcript fallback blocked by unavailable harness transcript source",
                harness_id=session.harness_id,
                detail=str(exc),
            )
            return None
        end = source.tail_position(session.session_id, spawn_cwd=spawn_cwd)
        start = TranscriptPosition(boundary.start_position) if boundary.start_position is not None else None
        lines = source.read_raw_lines(session.session_id, spawn_cwd=spawn_cwd, start=start, end=end)
        if not lines:
            return None
        return harness.sum_transcript_usage(lines, kind, model=lease.resolved_model)

    def _worker_boundary(self, lease_id: str, generation: int) -> InvocationBoundaryRecord | None:
        """This generation's own worker-starting boundary — whichever of spawn/resume/nudge
        actually opened it, since `record_worker`'s usage-kind label doesn't reliably name it."""
        for kind in _WORKER_STARTING_KINDS:
            boundary = self.invocation_boundaries.boundary(lease_id, generation, kind)
            if boundary is not None:
                return boundary
        return None

    def _resolved_harness(self, session: SessionReference) -> IHarnessUsageAccounting:
        """Resolve ``session``'s recorded owner — may raise ``UnknownHarnessError``/
        ``UnavailableHarnessError``. Every caller that can reach an unresolvable owner
        guards the resolution itself, first (e.g. ``DormantSession._resolve_harness``
        before ``record_worker``/``record_attempt``); this method never catches on their
        behalf, so it can never silently record against an owner it could not serve."""
        return self.harnesses.adapter(session.harness_id)
