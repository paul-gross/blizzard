"""The egress export's store seams — the usage read, the cursor's fact rows, the failure latch, and the ``events``
dataset's read-only projection of the derivation markers, their events and the drop facts."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from blizzard.foundation.event_log import EventLogKind
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.observability.analytics.events import DerivationMarker, DropFact
from blizzard.hub.domain.observability.egress.rows import AttributedUsage
from blizzard.hub.domain.observability.tracing.cursor import CursorKey

if TYPE_CHECKING:
    from blizzard.hub.domain.observability.egress.event_rows import EventDerivation


@domain_model
@dataclass(frozen=True, order=True)
class UsagePosition:
    """A position in the total order of usage facts: the time the hub received it, then its id."""

    recorded_at: datetime
    usage_id: int = 0


@domain_model
@dataclass(frozen=True, order=True)
class EventsPosition:
    """A position in the ``events`` dataset's total order: the source's time (``derived_at`` or ``dropped_at``),
    then the segment, then the extractor version, empty for a drop so it sorts before any derivation beside it."""

    at: datetime
    segment_id: str = ""
    extractor_version: str = ""


@domain_model
@dataclass(frozen=True)
class EgressCheckpoint:
    """One ``egress_cursor`` row: where a dataset stood after a pass, and what that pass wrote.

    ``step`` is set only for ``steps`` and ``events`` only for ``events``. ``files`` are the placed paths, the
    manifest last."""

    dataset: str
    step: CursorKey | None
    usage: UsagePosition
    row_count: int
    files: tuple[str, ...]
    recorded_at: datetime
    events: EventsPosition | None = None

    @property
    def position(self) -> datetime:
        """Where the dataset stood: its step cursor's instant, else its events cursor's, else its usage
        position's."""
        return self._derive_position()

    def _derive_position(self) -> datetime:
        if self.step is not None:
            return self.step.at
        if self.events is not None:
            return self.events.at
        return self.usage.recorded_at


@domain_model
@dataclass(frozen=True)
class EgressWriteFailure:
    """The newest ``egress-write-failed`` event: when it was recorded and what it said."""

    at: datetime
    message: str


class IReadEgress(Protocol):
    def newest_cursor(self, dataset: str) -> EgressCheckpoint | None:
        """The newest ``egress_cursor`` row of ``dataset`` — its position — or ``None`` before its first pass."""
        ...

    def newest_cursor_with_files(self) -> EgressCheckpoint | None:
        """The newest ``egress_cursor`` row of any dataset that placed files, or ``None`` before the first write."""
        ...

    def newest_egress_failure(self) -> EgressWriteFailure | None:
        """The newest ``egress-write-failed`` event, or ``None`` when the export has never failed."""
        ...

    def usage_after(self, position: UsagePosition, until: datetime, limit: int) -> Sequence[AttributedUsage]:
        """Up to ``limit`` usage facts past ``position`` and recorded at or before ``until``, in position order."""
        ...

    def newest_egress_latch(self) -> EventLogKind | None:
        """The kind of the newest ``egress-write-failed`` or ``egress-write-recovered`` event, or ``None``."""
        ...


class IWriteEgressCursor(IReadEgress, Protocol):
    def append_cursor(self, record: EgressCheckpoint) -> None: ...


@domain_model
@dataclass(frozen=True, order=True)
class EpochKey:
    """One chunk epoch, ordered by chunk id then epoch."""

    chunk_id: str
    epoch: int


class IReadEgressEvents(Protocol):
    """The ``events`` dataset's reads, read-only, in cursor order."""

    def markers_after(
        self, position: EventsPosition, until: datetime, limit: int, *, extractor_version: str | None
    ) -> Sequence[DerivationMarker]:
        """Up to ``limit`` markers past ``position`` derived at or before ``until``, in cursor order; only
        ``extractor_version``'s when one is named."""
        ...

    def drops_after(self, position: EventsPosition, until: datetime, limit: int) -> Sequence[DropFact]:
        """Up to ``limit`` drop facts past ``position`` dropped at or before ``until``, in cursor order."""
        ...

    def derivations(self, markers: Sequence[DerivationMarker]) -> Sequence[EventDerivation]:
        """Each marker's whole derivation, in ``markers``' order. A marker whose stored ``derived_at`` no longer
        matches once its events are loaded — re-derived or dropped meanwhile — is omitted, never mixed."""
        ...

    def epochs_minted_between(
        self, since: datetime, until: datetime, after: EpochKey | None, limit: int
    ) -> Sequence[EpochKey]:
        """Up to ``limit`` distinct epochs past ``after`` with a lease minted in ``[since, until)``, in key order."""
        ...

    def epoch_markers(self, epochs: Sequence[EpochKey], *, extractor_version: str | None) -> Sequence[DerivationMarker]:
        """Every marker of a segment recorded at one of ``epochs``; only ``extractor_version``'s when one is named."""
        ...

    def epoch_drops(self, epochs: Sequence[EpochKey]) -> Sequence[DropFact]:
        """Every drop fact recorded at one of ``epochs``."""
        ...
