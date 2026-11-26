"""The trace export's store seam — the closed-step reads and the cursor's fact rows."""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.hub.domain.chunk.ingest import IWorkRefLabels
from blizzard.hub.domain.chunk.model import WorkRefLabel
from blizzard.hub.domain.graph.model import IReadManyGraphs
from blizzard.hub.domain.observability.hub_events import HubEventKind
from blizzard.hub.domain.observability.tracing.cursor import CursorKey
from blizzard.hub.domain.observability.tracing.facts import StepFacts
from blizzard.hub.tenancy import TenantId


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
    """The runners' registered names."""

    def names_for(self, runner_ids: Iterable[str]) -> dict[str, str]:
        """Each runner's latest registered name, keyed by its id, in one batched read; an id with no
        registration is absent."""
        ...


@domain_model
@dataclass(frozen=True)
class ProjectLabel:
    """A project's slug and name as read."""

    slug: str
    name: str


class IReadProjectLabels(Protocol):
    """The projects' current slugs and names."""

    def labels_for(self, project_ids: Iterable[str]) -> dict[str, ProjectLabel]:
        """Each project's label, keyed by its id, in one batched read; an id with no project is absent."""
        ...


@dataclass(frozen=True)
class StepHydration:
    """One tenant's reads a chunk's step facts are completed through: the graphs, runner names and
    projects the facts name, and how the tenant renders a work ref and names the work source behind it — a
    configured source's current slug, keyed by the source a ref stores."""

    graphs: IReadManyGraphs
    names: IReadRunnerNames
    projects: IReadProjectLabels
    work_refs: IWorkRefLabels


class StepHydrations:
    """Each tenant's :class:`StepHydration`, through the typed tenant accessor the process root binds over its tenant
    scopes once they are built; it refuses a tenant as that tenant's scope refuses."""

    def __init__(self) -> None:
        self._open: Callable[[TenantId], StepHydration] | None = None

    def bind(self, open: Callable[[TenantId], StepHydration]) -> None:
        self._open = open

    def __call__(self, tenant: TenantId) -> StepHydration:
        assert self._open is not None  # bound by the process root before the process is handed out
        return self._open(tenant)


class LiveTracingConfig:
    """The ``[tracing]`` config the running pass bound, else the live resolution."""

    def __init__(self, read: Callable[[], TracingConfig]) -> None:
        self._read = read

    def current(self) -> TracingConfig:
        return self._read()


@domain_model
@dataclass(frozen=True)
class TraceExportFailure:
    """One ``trace-export-failed`` event: when it was recorded and its fixed message."""

    at: datetime
    message: str


class IReadStepCorpus(Protocol):
    """A window of closed steps across every open tenant."""

    def snapshot(self) -> AbstractContextManager[IReadStepCorpus]:
        """As :meth:`IReadTraceSteps.snapshot`, over the corpus."""
        ...

    def closing_candidates(self, since: datetime, until: datetime, limit: int) -> ClosingCandidates:
        """As :meth:`IReadTraceSteps.closing_candidates`, over every open tenant's closing facts."""
        ...

    def step_facts_for(
        self, chunk_ids: Sequence[str], bind: Callable[[TenantId, Collection[str]], WorkRefLabel]
    ) -> dict[str, StepFacts]:
        """Each existing chunk of an open tenant's :class:`StepFacts`, tagged with that tenant, keyed by chunk
        id. Its work refs render through ``bind``'s answer for that tenant, asked once a tenant with every
        source its chunks' refs name. ``graphs`` and ``runner_names`` are left unread."""
        ...


class IReadTraceSteps(Protocol):
    def snapshot(self) -> AbstractContextManager[IReadTraceSteps]:
        """These reads with their closing candidates and the corpus's fact rows all read as of one moment, so facts
        that committed together are read together or not at all and the candidates agree with the facts; a snapshot
        of them is the same moment. The hydration that completes the facts — graphs, runner names, project and
        work-source labels, the tenant's name — is read outside it, each tenant's on its own connections."""
        ...

    def closing_candidates(self, since: datetime, until: datetime, limit: int) -> ClosingCandidates:
        """The chunks with a closing fact recorded in ``[since, until]``, read from every closing-fact
        table in time order, ``limit`` rows a table — paging further only past a dense instant at ``since``."""
        ...

    def step_facts_for(self, chunk_ids: Sequence[str]) -> dict[str, StepFacts]:
        """Each existing chunk's whole :class:`StepFacts`, keyed by chunk id, in a fixed number of statements."""
        ...

    def closing_step_facts_for(self, chunk_ids: Sequence[str]) -> dict[str, StepFacts]:
        """As :meth:`step_facts_for`, completed only with the graphs that name each node — ``runner_names``, the
        project and tenant labels and the work-source labels are left unread. What telling a step's close or a
        chunk's finish needs, and what telling its spans does not."""
        ...

    def newest_cursor(self) -> TraceCheckpoint | None:
        """The newest ``trace_cursor`` row — the cursor's position — or ``None`` before the first pass."""
        ...

    def newest_export_latch(self) -> HubEventKind | None:
        """The kind of the newest ``trace-export-failed`` or ``trace-export-recovered`` event, or ``None``."""
        ...


class IReadTraceStatus(Protocol):
    """The trace export's newest told cursor and failure, read from stored facts."""

    def newest_export_cursor(self) -> TraceCheckpoint | None:
        """The newest ``trace_cursor`` row that told at least one span, or ``None`` if none ever did."""
        ...

    def newest_export_failure(self) -> TraceExportFailure | None:
        """The newest ``trace-export-failed`` event, or ``None``."""
        ...


class IWriteTraceCursor(IReadTraceSteps, Protocol):
    def append_cursor(self, record: TraceCheckpoint) -> None: ...
