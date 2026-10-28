"""The hub's own event log — the occurrences of the hub's machinery, which concern no tenant, chunk or runner: its
exports' failures, recoveries, skips, rejections and resets, its startup and migration, and a tenant teardown's close
and completion (``blizzard-product:/plans/multi-tenancy/hub/spec/store.md`` §The hub's own event log).

A hub occurrence is recorded in ``hub_event_log`` alone and published to no stream: every live stream is one
tenant's, and no tenant sees the hub's occurrences."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, cast, get_args

from blizzard.foundation.event_log import EventLogSeverity
from blizzard.foundation.roles import domain_model

#: The kinds of the hub's own occurrences; the export kinds stay in ``EventLogKind`` too, for a runner's own exports.
HubEventKind = Literal[
    "trace-export-failed",
    "trace-export-recovered",
    "trace-window-skipped",
    "trace-config-rejected",
    "egress-write-failed",
    "egress-write-recovered",
    "egress-config-rejected",
    "egress-cursor-reset",
    "hub-started",
    "hub-migrated",
    "tenant-closed",
    "tenant-deleted",
]

#: Each hub kind's one severity — an export kind's is the one ``EVENT_LOG_SEVERITY`` gives it.
HUB_EVENT_SEVERITY: Mapping[HubEventKind, EventLogSeverity] = {
    "trace-export-failed": EventLogSeverity.WARNING,
    "trace-export-recovered": EventLogSeverity.INFO,
    "trace-window-skipped": EventLogSeverity.WARNING,
    "trace-config-rejected": EventLogSeverity.WARNING,
    "egress-write-failed": EventLogSeverity.WARNING,
    "egress-write-recovered": EventLogSeverity.INFO,
    "egress-config-rejected": EventLogSeverity.WARNING,
    "egress-cursor-reset": EventLogSeverity.INFO,
    "hub-started": EventLogSeverity.INFO,
    "hub-migrated": EventLogSeverity.INFO,
    "tenant-closed": EventLogSeverity.INFO,
    "tenant-deleted": EventLogSeverity.INFO,
}

_HUB_EVENT_KINDS = frozenset(get_args(HubEventKind))


def narrow_hub_event_kind(value: str) -> HubEventKind | None:
    """``value`` narrowed to :data:`HubEventKind`, or ``None`` outside the closed vocabulary."""
    return cast(HubEventKind, value) if value in _HUB_EVENT_KINDS else None


@domain_model
@dataclass(frozen=True)
class HubEvent:
    """One ``hub_event_log`` row: its kind, when it was recorded, and its message."""

    kind: HubEventKind
    at: datetime
    message: str


@domain_model
@dataclass(frozen=True)
class HubEventEntry:
    """One ``hub_event_log`` row as a hub administrator reads it: its id, severity, kind, time, message and detail."""

    event_id: int
    severity: EventLogSeverity
    kind: HubEventKind
    at: datetime
    message: str
    detail: dict[str, object] | None


class IReadHubEventLog(Protocol):
    """The hub's own event log, read newest-first."""

    def recent(self, limit: int) -> list[HubEventEntry]:
        """The newest ``limit`` rows, newest first — the hub administrators' read of the log."""
        ...

    def newest(self, kinds: Sequence[HubEventKind]) -> HubEvent | None:
        """The newest event of any of ``kinds`` — filtered in the query, so a newer event of another kind never hides
        it — or ``None`` when none was ever recorded."""
        ...


class IWriteHubEventLog(IReadHubEventLog, Protocol):
    def append(
        self,
        *,
        severity: EventLogSeverity,
        kind: HubEventKind,
        message: str,
        detail: Mapping[str, object] | None,
        at: datetime,
    ) -> int:
        """Append one ``hub_event_log`` row — never mutated once written; ``detail`` is serialized to JSON text by
        the store. Returns the row's id."""
        ...


class HubEventLogService:
    """Record one of the hub's own occurrences, its severity derived from its kind."""

    def __init__(self, *, events: IWriteHubEventLog) -> None:
        self._events = events

    def record(self, *, kind: HubEventKind, message: str, detail: Mapping[str, object] | None, at: datetime) -> int:
        """Append the row and return its id."""
        return self._events.append(
            severity=HUB_EVENT_SEVERITY[kind],
            kind=kind,
            message=message,
            detail=dict(detail) if detail is not None else None,
            at=at,
        )


__all__ = [
    "HUB_EVENT_SEVERITY",
    "HubEvent",
    "HubEventEntry",
    "HubEventKind",
    "HubEventLogService",
    "IReadHubEventLog",
    "IWriteHubEventLog",
    "narrow_hub_event_kind",
]
