"""The hub's own event log — the occurrences of the hub's machinery, which concern no tenant, chunk or runner: its
exports' failures, recoveries, skips, rejections and resets, its startup and migration, and a tenant teardown's close
and completion (``blizzard-product:/plans/multi-tenancy/hub/spec/store.md`` §The hub's own event log).

A hub occurrence is recorded in ``hub_event_log`` alone and published to no stream: every live stream is one
tenant's, and no tenant sees the hub's occurrences."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.event_log import HUB_EVENT_SEVERITY, EventLogSeverity, HubEventKind
from blizzard.foundation.roles import domain_model


@domain_model
@dataclass(frozen=True)
class HubEvent:
    """One ``hub_event_log`` row: its kind, when it was recorded, and its message."""

    kind: HubEventKind
    at: datetime
    message: str


class IReadHubEventLog(Protocol):
    """The hub's own event log, read newest-first."""

    def newest(self, kinds: Sequence[HubEventKind]) -> HubEvent | None:
        """The newest event of any of ``kinds`` — filtered in the query, so a newer event of another kind never hides
        it — or ``None`` when none was ever recorded."""
        ...


class IWriteHubEventLog(IReadHubEventLog, Protocol):
    def append(
        self, *, severity: EventLogSeverity, kind: HubEventKind, message: str, detail: dict | None, at: datetime
    ) -> int:
        """Append one ``hub_event_log`` row — never mutated once written; ``detail`` is serialized to JSON text by
        the store. Returns the row's id."""
        ...


class HubEventLogService:
    """Record one of the hub's own occurrences, its severity derived from its kind."""

    def __init__(self, *, events: IWriteHubEventLog) -> None:
        self._events = events

    def record(self, *, kind: HubEventKind, message: str, detail: dict | None, at: datetime) -> int:  # type: ignore[type-arg]
        """Append the row and return its id."""
        return self._events.append(severity=HUB_EVENT_SEVERITY[kind], kind=kind, message=message, detail=detail, at=at)


__all__ = ["HubEvent", "HubEventLogService", "IReadHubEventLog", "IWriteHubEventLog"]
