"""The trace export's store seam — the closed-step reads and the cursor's fact rows."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.event_log import EventLogKind
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.observability.tracing.cursor import CursorKey
from blizzard.hub.domain.observability.tracing.facts import StepFacts


@domain_model
@dataclass(frozen=True)
class TraceCheckpoint:
    """One ``trace_cursor`` row: where the cursor stood after a pass, and what that pass told."""

    position: CursorKey
    span_count: int
    recorded_at: datetime


@domain_model
@dataclass(frozen=True)
class ClosingCandidates:
    """The chunks holding a closing fact in a window, and how far that read is complete.

    ``frontier``: a closing-fact table held more rows than the limit, so a step closing at or after it
    may sit in an unread chunk; always later than ``since``. ``newest``: the latest closing instant read."""

    chunk_ids: tuple[str, ...]
    frontier: datetime | None = None
    newest: datetime | None = None


class IReadRunnerNames(Protocol):
    """The runners' names, for the spans that carry their ids."""

    def names_for(self, runner_ids: Iterable[str]) -> dict[str, str]:
        """Each runner's latest registered name, keyed by its id, in one batched read; an id with no
        registration is absent."""
        ...


@domain_model
@dataclass(frozen=True)
class TraceExportFailure:
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

    def newest_cursor(self) -> TraceCheckpoint | None:
        """The newest ``trace_cursor`` row — the cursor's position — or ``None`` before the first pass."""
        ...

    def newest_export_latch(self) -> EventLogKind | None:
        """The kind of the newest ``trace-export-failed`` or ``trace-export-recovered`` event, or ``None``."""
        ...


class IReadTraceStatus(Protocol):
    """What the operator status reads beyond the cursor and latch :class:`IReadTraceSteps` already holds —
    facts the sweep left behind, never process memory."""

    def newest_export_cursor(self) -> TraceCheckpoint | None:
        """The newest ``trace_cursor`` row that told at least one span, or ``None`` if none ever did."""
        ...

    def newest_export_failure(self) -> TraceExportFailure | None:
        """The newest ``trace-export-failed`` event, or ``None``."""
        ...


class IWriteTraceCursor(IReadTraceSteps, Protocol):
    def append_cursor(self, record: TraceCheckpoint) -> None: ...
