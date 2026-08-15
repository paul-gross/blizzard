"""The runner event-publishing seam (D2/D4, blizzard#317) — the inner-layer Protocol the
loop and domain layers depend on instead of the concrete :class:`~blizzard.runner.events.broker.EventBroker`
(``bzh:dependency-inversion``). Every mutation seam that publishes holds this, never the
concrete class, which stays a composition-root-only import (``cli.py``, ``app.py``,
``loop/build.py``, ``api/wiring.py``)."""

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

    def publish_lease_changed(
        self,
        lease_id: str,
        chunk_id: str,
        *,
        cause: LeaseChangeCause,
        node_name: str | None = None,
        key: str | None = None,
    ) -> int: ...

    def publish_ask_changed(
        self, lease_id: str, chunk_id: str, question_id: str, *, cause: AskChangeCause, key: str | None = None
    ) -> int: ...

    def publish_escalation_changed(
        self, chunk_id: str, *, cause: EscalationChangeCause, lease_id: str | None = None, key: str | None = None
    ) -> int: ...

    def publish_takeover_changed(
        self, chunk_id: str, takeover_id: str, *, cause: TakeoverChangeCause, key: str | None = None
    ) -> int: ...

    def publish_environment_changed(
        self, chunk_id: str, environment_id: str, *, cause: EnvironmentChangeCause, key: str | None = None
    ) -> int: ...

    def publish_fact_changed(
        self, *, seq: int, kind: str, chunk_id: str | None, lease_id: str | None, key: str | None = None
    ) -> int: ...
