"""The egress export's store seam — the usage read, the cursor's fact rows, and the failure latch.

Closed steps are read through the trace sweep's own seam (``IReadTraceSteps``), so the two exports never
disagree about a step."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.event_log import EventLogKind
from blizzard.hub.domain.egress.rows import UsageRow
from blizzard.hub.domain.tracing.cursor import CursorKey


@dataclass(frozen=True, order=True)
class UsagePosition:
    """A position in the total order of usage facts: the time the hub received it, then its id."""

    recorded_at: datetime
    usage_id: int = 0


@dataclass(frozen=True)
class EgressCursorRecord:
    """One ``egress_cursor`` row: where a dataset stood after a pass, and what that pass wrote.

    ``step`` is ``None`` for ``invocations``. ``files`` are the placed paths, the manifest last."""

    dataset: str
    step: CursorKey | None
    usage: UsagePosition
    row_count: int
    files: tuple[str, ...]
    recorded_at: datetime


@dataclass(frozen=True)
class EgressFailureRecord:
    """The newest ``egress-write-failed`` event: when it was recorded and what it said."""

    at: datetime
    message: str


class IReadEgress(Protocol):
    def newest_cursor(self, dataset: str) -> EgressCursorRecord | None:
        """The newest ``egress_cursor`` row of ``dataset`` — its position — or ``None`` before its first pass."""
        ...

    def newest_cursor_with_files(self) -> EgressCursorRecord | None:
        """The newest ``egress_cursor`` row of any dataset that placed files, or ``None`` before the first write."""
        ...

    def newest_egress_failure(self) -> EgressFailureRecord | None:
        """The newest ``egress-write-failed`` event, or ``None`` when the export has never failed."""
        ...

    def usage_after(self, position: UsagePosition, until: datetime, limit: int) -> Sequence[UsageRow]:
        """Up to ``limit`` usage facts past ``position`` and recorded at or before ``until``, in position order."""
        ...

    def newest_egress_latch(self) -> EventLogKind | None:
        """The kind of the newest ``egress-write-failed`` or ``egress-write-recovered`` event, or ``None``."""
        ...


class IWriteEgressCursor(IReadEgress, Protocol):
    def append_cursor(self, record: EgressCursorRecord) -> None: ...
