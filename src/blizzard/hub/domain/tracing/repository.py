"""The trace export's store seam — the closed-step reads and the cursor's fact rows."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.event_log import EventLogKind
from blizzard.hub.domain.tracing.cursor import CursorKey
from blizzard.hub.domain.tracing.facts import StepFacts
from blizzard.hub.domain.work import WorkRef

#: A work ref's source-native token (``acme#42``), or ``None`` when no configured source renders it.
WorkRefLabel = Callable[[WorkRef], str | None]


@dataclass(frozen=True)
class TraceCursorRecord:
    """One ``trace_cursor`` row: where the cursor stood after a pass, and what that pass told."""

    position: CursorKey
    span_count: int
    recorded_at: datetime


@dataclass(frozen=True)
class ClosingCandidates:
    """The chunks holding a closing fact in a window, and how far that read is complete.

    ``frontier`` is set when a closing-fact table held more rows than the read's limit: a step
    closing at or after it may sit in an unread chunk. It is always later than the read's ``since``."""

    chunk_ids: tuple[str, ...]
    frontier: datetime | None = None


@dataclass(frozen=True)
class TraceFailureRecord:
    """One ``trace-export-failed`` event: when it was recorded and its fixed message."""

    at: datetime
    message: str


class IReadTraceSteps(Protocol):
    def closing_candidates(self, since: datetime, until: datetime, limit: int) -> ClosingCandidates:
        """The chunks with a closing fact recorded in ``[since, until]``, read from every closing-fact
        table in time order, ``limit`` rows a table — paging further only past a dense instant at ``since``."""
        ...

    def step_facts_for(self, chunk_ids: Sequence[str]) -> dict[str, StepFacts]:
        """Each existing chunk's whole :class:`StepFacts`, keyed by chunk id, in a fixed number of statements."""
        ...

    def newest_cursor(self) -> TraceCursorRecord | None:
        """The newest ``trace_cursor`` row — the cursor's position — or ``None`` before the first pass."""
        ...

    def newest_export_latch(self) -> EventLogKind | None:
        """The kind of the newest ``trace-export-failed`` or ``trace-export-recovered`` event, or ``None``."""
        ...


class IReadTraceStatus(Protocol):
    """What the operator status reads beyond the cursor and latch :class:`IReadTraceSteps` already holds —
    facts the sweep left behind, never process memory."""

    def newest_export_cursor(self) -> TraceCursorRecord | None:
        """The newest ``trace_cursor`` row that told at least one span, or ``None`` if none ever did."""
        ...

    def newest_export_failure(self) -> TraceFailureRecord | None:
        """The newest ``trace-export-failed`` event, or ``None``."""
        ...


class IWriteTraceCursor(IReadTraceSteps, Protocol):
    def append_cursor(self, record: TraceCursorRecord) -> None: ...
