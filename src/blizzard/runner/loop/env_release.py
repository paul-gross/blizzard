"""Handing a chunk's workspace environments back to the provider, and the facts that record it."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.clock import IClock
from blizzard.runner.domain.leases import IReadLeaseRepository
from blizzard.runner.environments.provider import AcquiredEnvironment, IWorkspaceProvider
from blizzard.runner.environments.repository import IWriteEnvironmentRepository
from blizzard.runner.events.publisher import IRunnerEventPublisher
from blizzard.runner.loop.worker_stdout import WorkerStdoutFiles


@dataclass(frozen=True)
class EnvironmentRelease:
    """The two ways a runner gives an environment back — at the chunk's tenure end, and
    when a just-recorded binding's claim never landed."""

    environments: IWriteEnvironmentRepository
    leases: IReadLeaseRepository
    clock: IClock
    provider: IWorkspaceProvider
    worker_files: WorkerStdoutFiles
    #: The SSE publish seam (D2, blizzard#317), typed against the Protocol
    #: (``bzh:dependency-inversion``); ``None`` on a loop-only caller, a no-op there.
    events: IRunnerEventPublisher | None = None

    def release_chunk(self, chunk_id: str) -> None:
        """Release every held environment at the chunk's tenure end, and sweep the
        per-generation stdout files of every lease it ever minted (issue #58) — bounded, and
        no longer needed once their usage facts are durable."""
        now = self.clock.now()
        for binding in self.environments.bindings_for_chunk(chunk_id):
            self.provider.release(binding.environment_id)
            self.environments.record_release(chunk_id=chunk_id, environment_id=binding.environment_id, released_at=now)
            self._publish_released(chunk_id, binding.environment_id)
        for lease_id in self.leases.lease_ids_for_chunk(chunk_id):
            self.worker_files.cleanup(lease_id)

    def release_binding(self, chunk_id: str, acquired: list[AcquiredEnvironment]) -> None:
        """Undo a just-recorded binding whose claim never landed — release the fact and the env.

        The binding is written before the hub claim, so a claim that fails to send or loses the
        race must retract both the local binding fact and the provider allocation, leaving the
        chunk exactly as if it had never been touched (it stays ``ready``)."""
        now = self.clock.now()
        for a in acquired:
            self.environments.record_release(chunk_id=chunk_id, environment_id=a.environment_id, released_at=now)
            self._publish_released(chunk_id, a.environment_id)
            self.provider.release(a.environment_id)

    def _publish_released(self, chunk_id: str, environment_id: str) -> None:
        if self.events is not None:
            self.events.publish_environment_changed(chunk_id, environment_id, cause="released")
