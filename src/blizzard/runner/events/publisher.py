"""The runner event-publishing seam (D2/D4, blizzard#317) — the inner-layer Protocol every
publishing mutation seam holds in place of the concrete
:class:`~blizzard.runner.events.broker.EventBroker` (``bzh:dependency-inversion``), which
stays a composition-root-only import. ``scripts/runner_event_census.py`` owns the
inventory of who holds this seam."""

from __future__ import annotations

from typing import Protocol

from blizzard.wire.sse_runner import (
    AskChangeCause,
    EnvironmentChangeCause,
    EscalationChangeCause,
    LeaseChangeCause,
    TakeoverChangeCause,
)


class IRunnerEventPublisher(Protocol):
    """The six ``publish_*`` calls a runner mutation seam may make. Structurally satisfied by
    :class:`~blizzard.runner.events.broker.EventBroker` — no explicit inheritance needed."""

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
