"""Handing a chunk's workspace environments back to the provider, and the facts that record it."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.clock import IClock
from blizzard.runner.environments.provider import AcquiredEnvironment, IWorkspaceProvider
from blizzard.runner.environments.repository import IWriteEnvironmentRepository, release_instants
from blizzard.runner.events.publisher import IRunnerEventPublisher


@dataclass(frozen=True)
class EnvironmentRelease:
    """The two ways a runner gives an environment back — at the chunk's tenure end, and
    when a just-recorded binding's claim never landed."""

    environments: IWriteEnvironmentRepository
    clock: IClock
    provider: IWorkspaceProvider
    #: The SSE publish seam, typed against the Protocol
    #: (``bzh:dependency-inversion``); ``None`` on a loop-only caller, a no-op there.
    events: IRunnerEventPublisher | None = None

    def release_chunk(self, chunk_id: str) -> None:
        """Release every environment held at the chunk's tenure end; the lease's worker
        stdout/stderr is left in place."""
        held = self.environments.bindings_for_chunk(chunk_id)
        for environment_id, released_at in release_instants(
            held, [binding.environment_id for binding in held], self.clock.now()
        ):
            self.provider.release(environment_id)
            self.environments.record_release(chunk_id=chunk_id, environment_id=environment_id, released_at=released_at)
            self._publish_released(chunk_id, environment_id)

    def release_binding(self, chunk_id: str, acquired: list[AcquiredEnvironment]) -> None:
        """Undo a just-recorded binding whose claim never landed — release the fact and the env.

        The binding is written before the hub claim, so a claim that fails to send or loses the
        race must retract both the local binding fact and the provider allocation, leaving the
        chunk exactly as if it had never been touched (it stays ``ready``)."""
        released = dict(
            release_instants(
                self.environments.bindings_for_chunk(chunk_id), [a.environment_id for a in acquired], self.clock.now()
            )
        )
        for a in acquired:
            released_at = released.get(a.environment_id)
            if released_at is not None:
                self.environments.record_release(
                    chunk_id=chunk_id, environment_id=a.environment_id, released_at=released_at
                )
                self._publish_released(chunk_id, a.environment_id)
            self.provider.release(a.environment_id)

    def _publish_released(self, chunk_id: str, environment_id: str) -> None:
        if self.events is not None:
            self.events.publish_environment_changed(chunk_id, environment_id, cause="released")
