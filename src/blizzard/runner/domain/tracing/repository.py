"""The lease trace seam — closed-lease facts, the sweep's window, and the cursor's and latch's fact rows."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.event_log import EventLogKind
from blizzard.runner.domain.tracing.cursor import LeaseCursorKey
from blizzard.runner.domain.tracing.facts import LeaseTraceFacts


@dataclass(frozen=True)
class LeaseCursorRecord:
    """One ``trace_cursor`` row: where the cursor stood after a pass, and what that pass told."""

    position: LeaseCursorKey
    span_count: int
    recorded_at: datetime


@dataclass(frozen=True)
class LeaseFailureRecord:
    """One ``trace-export-failed`` latch row: when the outage began."""

    at: datetime


class IReadLeaseTraceFacts(Protocol):
    def lease_trace_facts(self, lease_id: str) -> LeaseTraceFacts | None:
        """The lease's facts, or ``None`` when it is unknown or has no closure yet."""
        ...

    def lease_trace_facts_for(self, lease_ids: Collection[str]) -> dict[str, LeaseTraceFacts]:
        """Each closed lease's facts keyed by lease id, in a fixed number of statements per batch;
        an unknown or unclosed id is absent."""
        ...


class IReadLeaseTraceCursor(Protocol):
    def closed_leases_after(self, since: LeaseCursorKey, until: datetime, limit: int) -> tuple[LeaseCursorKey, ...]:
        """The first ``limit`` leases in key order whose first closure is after ``since``, at or before ``until``."""
        ...

    def oldest_unsent_lease(self, since: LeaseCursorKey, until: datetime) -> LeaseCursorKey | None:
        """The first lease :meth:`closed_leases_after` would read, or ``None``."""
        ...

    def newest_trace_cursor(self) -> LeaseCursorRecord | None:
        """The newest ``trace_cursor`` row — the cursor's position — or ``None`` before the first pass."""
        ...

    def newest_trace_latch(self) -> EventLogKind | None:
        """The kind of the newest ``trace_export_latch`` row, or ``None``."""
        ...

    def newest_export_cursor(self) -> LeaseCursorRecord | None:
        """The newest ``trace_cursor`` row that told at least one span, or ``None`` if none ever did."""
        ...

    def newest_export_failure(self) -> LeaseFailureRecord | None:
        """The newest ``trace-export-failed`` latch row, or ``None``."""
        ...


class IReadLeaseTraces(IReadLeaseTraceFacts, IReadLeaseTraceCursor, Protocol):
    """Every lease trace read, composed."""


class IWriteLeaseTraces(IReadLeaseTraces, Protocol):
    def append_trace_cursor(self, record: LeaseCursorRecord) -> None: ...

    def record_trace_latch(self, kind: EventLogKind, *, at: datetime, report_kind: str, report_payload: str) -> int:
        """Append the latch row and its hub-bound report in one transaction; return the report's seq."""
        ...
