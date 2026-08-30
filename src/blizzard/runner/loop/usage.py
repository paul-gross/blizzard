"""One attempt's harness token usage, parsed off its own output and made durable."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.clock import IClock
from blizzard.runner.domain.leases import LeaseRecord
from blizzard.runner.environments.repository import EnvBindingRecord
from blizzard.runner.events.publisher import IRunnerEventPublisher
from blizzard.runner.harness.adapter import IHarnessAdapter
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.harness.transcript import IHarnessTranscriptSource
from blizzard.runner.harness.usage import UsageKind, UsageSample
from blizzard.runner.loop.worker_stdout import WorkerStdoutFiles
from blizzard.runner.stores import IWriteRunnerStore
from blizzard.wire.facts import USAGE_RECORDED


@dataclass(frozen=True)
class UsageRecorder:
    """Records a lease's usage facts, keyed ``(lease, generation, kind)`` so they are
    idempotent across a re-run and a crash finds each durable or absent."""

    store: IWriteRunnerStore
    clock: IClock
    harness: IHarnessAdapter
    worker_files: WorkerStdoutFiles
    workspace_root: str
    transcripts: IHarnessTranscriptSource | None = None
    #: The SSE publish seam (D2, blizzard#317), typed against the Protocol
    #: (``bzh:dependency-inversion``); ``None`` on a loop-only caller, a no-op there.
    events: IRunnerEventPublisher | None = None

    def record_worker(self, lease: LeaseRecord, bindings: list[EnvBindingRecord]) -> None:
        """Record just this attempt's spawn/resume invocation usage — no judgement ran."""
        generation = self.store.lease_generation(lease.lease_id)
        kind: UsageKind = "spawn" if generation <= 1 else "resume"
        sample = self._worker_sample(lease, bindings, generation=generation, kind=kind)
        if sample is not None:
            self.record_sample(lease, generation=generation, sample=sample)

    def record_attempt(self, lease: LeaseRecord, bindings: list[EnvBindingRecord], *, judge_output: str) -> None:
        """Record the spawn/resume invocation ADVANCE is judging and the judgement resume
        that elicited its verdict — each its own fact."""
        self.record_worker(lease, bindings)
        generation = self.store.lease_generation(lease.lease_id)
        # Attribute to the lease's own `resolved_model` stamp (issue #144), not the adapter
        # default: a judge turn on a sonnet session would otherwise book its spend against opus.
        judge_sample = self.harness.parse_usage(judge_output, "judge", model=lease.resolved_model)
        if judge_sample is not None:
            self.record_sample(lease, generation=generation, sample=judge_sample)

    def record_sample(self, lease: LeaseRecord, *, generation: int, sample: UsageSample) -> None:
        """Make one already-parsed sample durable against this lease's generation."""
        seq = self.store.record_usage(
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
        # Same attribution fallback as the judge fact (issue #144): on a resume the stamp is
        # what the session was MINTED with, not what a fresh resolution would produce now.
        sample = self.harness.parse_usage(output, kind, model=lease.resolved_model) if output else None
        if sample is not None:
            return sample
        if lease.session_id is None or self.transcripts is None:
            return None
        fallback_workdir = bindings[0].workdir if bindings else None
        spawn_cwd = SpawnCwd(self.workspace_root, fallback_workdir).path
        lines = self.transcripts.read_raw_lines(lease.session_id, spawn_cwd=spawn_cwd)
        if not lines:
            return None
        return self.harness.sum_transcript_usage(lines, kind, model=lease.resolved_model)
