"""The runner event-publishing seam (``bzh:dependency-inversion``)."""

from __future__ import annotations

from typing import Protocol

from blizzard.foundation.runner_event_types import (
    AskChangeCause,
    EnvironmentChangeCause,
    EscalationChangeCause,
    LeaseChangeCause,
    TakeoverChangeCause,
)


class IRunnerEventPublisher(Protocol):
    """The ``publish_*`` calls a runner mutation seam may make; each returns the published event's sequence number."""

    def publish_lease_changed(self, lease_id: str, chunk_id: str, *, cause: LeaseChangeCause) -> int: ...

    def publish_ask_changed(self, lease_id: str, chunk_id: str, question_id: str, *, cause: AskChangeCause) -> int: ...

    def publish_escalation_changed(
        self, chunk_id: str, *, cause: EscalationChangeCause, lease_id: str | None = None
    ) -> int: ...

    def publish_takeover_changed(self, chunk_id: str, takeover_id: str, *, cause: TakeoverChangeCause) -> int: ...

    def publish_environment_changed(
        self, chunk_id: str, environment_id: str, *, cause: EnvironmentChangeCause
    ) -> int: ...

    def publish_fact_changed(self, *, seq: int, kind: str, chunk_id: str | None, lease_id: str | None) -> int: ...
