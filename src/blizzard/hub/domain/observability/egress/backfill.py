"""Backfill: writes the rows of a past window, as the live export would have, without moving a cursor.

Contract: ``blizzard-product:/plans/fact-egress/steps/spec/export.md`` §Operator surface. It holds read seams
alone — never :class:`IWriteEgressCursor` — so it cannot move a cursor by construction. It pages the reads the live
pass uses from a local position that is never stored, assembles each row through the live sweep's own functions,
and writes through a writer of its own (the writer is single-caller, and its file names derive from its token), so it
never contends with the sweep or with another backfill. It reconciles no late usage: it assembles from the record
as it stands. ``events`` selects by step start, not derivation time (``fact-egress/events/spec/export.md``
§Backfill)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import domain_model, dto
from blizzard.hub.config import EgressConfig
from blizzard.hub.domain.observability.analytics.extraction import EXTRACTOR_VERSION
from blizzard.hub.domain.observability.egress.assembly import (
    guarded,
    invocation_rows,
    sorted_step_rows,
    window_step_rows,
)
from blizzard.hub.domain.observability.egress.event_rows import FilePathPolicy, missing_key_reason
from blizzard.hub.domain.observability.egress.events_window import events_rows, position_of
from blizzard.hub.domain.observability.egress.lifecycle import ExportVerb, export_allows
from blizzard.hub.domain.observability.egress.repository import EpochKey, IReadEgress, IReadEgressEvents, UsagePosition
from blizzard.hub.domain.observability.egress.schema import EVENTS_SCHEMA, INVOCATIONS_SCHEMA, STEPS_SCHEMA
from blizzard.hub.domain.observability.operator_window import OperatorWindow, fault_message
from blizzard.hub.domain.observability.tracing.cursor import CursorKey
from blizzard.hub.domain.observability.tracing.repository import IReadTraceSteps
from blizzard.hub.domain.observability.tracing.window import read_window
from blizzard.hub.egress.writer import (
    DatasetSchema,
    EgressBatch,
    EgressFailure,
    EgressPass,
    EgressValues,
    IEgressWriter,
    PlacedFile,
)

_ONE_MICROSECOND = timedelta(microseconds=1)
_Rows = list[tuple[date, EgressValues]]


class BackfillWindowRefused(ValueError):
    """The window is inverted, empty, wider than ``backfill_max_window``, reaches past now, or names a dataset
    not configured."""


class BackfillUnavailable(Exception):
    """A wet backfill while the export is off or rejected: there is no writer to backfill through."""


def require_backfillable(
    since: datetime,
    until: datetime,
    dataset: str | None,
    *,
    datasets: Sequence[str],
    max_window_seconds: int,
    now: datetime,
    dry_run: bool,
    writers_wired: bool,
    missing_path_key: str | None = None,
) -> None:
    """Refuse a backfill the wiring, window, or dataset cannot honour — a wet run with the export
    off first, then the window, then the events dataset while its path hash key is missing, then a
    dataset not configured. A dry run only counts the rows and files it would place, so it runs
    with the export off."""
    if not export_allows(ExportVerb.DRY_BACKFILL if dry_run else ExportVerb.BACKFILL, wired=writers_wired):
        raise BackfillUnavailable("the egress export is not configured; there is nowhere to write a backfill")
    fault = OperatorWindow(since, until).fault(max_window=timedelta(seconds=max_window_seconds), now=now)
    if fault is not None:
        raise BackfillWindowRefused(
            fault_message(fault, max_window_name="backfill_max_window", max_window_seconds=max_window_seconds)
        )
    if dataset == EVENTS_SCHEMA.name and missing_path_key is not None:
        raise BackfillWindowRefused(missing_key_reason(missing_path_key))
    if dataset is not None and dataset not in datasets:
        raise BackfillWindowRefused(f"dataset {dataset!r} is not configured; the export writes {', '.join(datasets)}")


def files_needed(partition_rows: Sequence[int], max_rows_per_file: int) -> int:
    """How many files a pass places: each partition's rows split into files of at most ``max_rows_per_file``."""
    return sum(-(-rows // max_rows_per_file) for rows in partition_rows)


@dto
@dataclass(frozen=True)
class DatasetCount:
    """What a backfill wrote, or with ``dry_run`` would have written, of one dataset: rows and data files."""

    dataset: str
    rows: int
    files: int


@dto
@dataclass(frozen=True)
class BackfillResult:
    """The per-dataset counts. ``failure`` is set when the writer refused or raised: the counts are then what was
    committed before, and the backfill stopped."""

    dry_run: bool
    datasets: tuple[DatasetCount, ...]
    failure: EgressFailure | None = None


@domain_model
@dataclass
class _Tally:
    rows: int = 0
    files: int = 0


@dataclass
class _Run:
    """One backfill's state: its window, its writer (``None`` for a dry run), and its counts."""

    since: datetime
    until: datetime
    writer: IEgressWriter | None
    now: datetime
    tallies: dict[str, _Tally] = field(default_factory=dict)

    def result(self, failure: EgressFailure | None = None) -> BackfillResult:
        counts = tuple(DatasetCount(name, tally.rows, tally.files) for name, tally in self.tallies.items())
        return BackfillResult(self.writer is None, counts, failure)


class EgressBackfill:
    def __init__(
        self,
        *,
        steps: IReadTraceSteps,
        egress: IReadEgress,
        event_reads: IReadEgressEvents,
        paths: FilePathPolicy | None,
        clock: IClock,
        config: EgressConfig,
        writers: Callable[[], IEgressWriter] | None,
        missing_path_key: str | None = None,
    ) -> None:
        if EVENTS_SCHEMA.name in config.datasets and paths is None:
            raise ValueError("the events dataset needs a file path policy")
        self._steps = steps
        self._egress = egress
        self._event_reads = event_reads
        self._paths = paths
        self._extractor_version = EXTRACTOR_VERSION if config.extractor_versions == "current" else None
        self._clock = clock
        self._datasets = config.datasets
        self._batch_limit = config.batch_limit
        self._max_rows_per_file = config.max_rows_per_file
        self._max_window_seconds = config.backfill_max_window
        self._writers = writers
        self._missing_path_key = missing_path_key

    def backfill(self, since: datetime, until: datetime, *, dataset: str | None, dry_run: bool) -> BackfillResult:
        now = self._clock.now()
        require_backfillable(
            since,
            until,
            dataset,
            datasets=self._datasets,
            max_window_seconds=self._max_window_seconds,
            now=now,
            dry_run=dry_run,
            writers_wired=self._writers is not None,
            missing_path_key=self._missing_path_key,
        )
        writers = self._writers
        run = _Run(since, until, writers() if not dry_run and writers is not None else None, now)
        for name in self._datasets:
            if dataset is not None and name != dataset:
                continue
            run.tallies[name] = _Tally()
            failure = self._window(name, run)
            if failure is not None:
                return run.result(failure)
        return run.result()

    def _window(self, dataset: str, run: _Run) -> EgressFailure | None:
        if dataset == STEPS_SCHEMA.name:
            return self._steps_window(run)
        if dataset == INVOCATIONS_SCHEMA.name:
            return self._invocations_window(run)
        if dataset == EVENTS_SCHEMA.name:
            return self._events_window(run)
        raise BackfillWindowRefused(f"dataset {dataset!r} cannot be backfilled")

    # --- steps --------------------------------------------------------------------------

    def _steps_window(self, run: _Run) -> EgressFailure | None:
        position = CursorKey.opening(run.since)
        while True:
            # The window is half-open: read_window's own bound is inclusive.
            window = read_window(self._steps, position, run.until - _ONE_MICROSECOND, self._batch_limit)
            rows = sorted_step_rows(window_step_rows(window, run.now))
            if rows and (failure := self._place(run, STEPS_SCHEMA, rows)) is not None:
                return failure
            if window.position == position:
                return None
            position = window.position

    # --- invocations --------------------------------------------------------------------

    def _invocations_window(self, run: _Run) -> EgressFailure | None:
        # usage_after is exclusive of its position; usage ids start at 1, so id 0 admits every usage at ``since``.
        position = UsagePosition(run.since)
        while True:
            usage = self._egress.usage_after(position, run.until - _ONE_MICROSECOND, self._batch_limit)
            if not usage:
                return None
            facts = self._steps.step_facts_for(sorted({row.chunk_id for row in usage}))
            rows = invocation_rows(usage, facts, run.now).rows
            if rows and (failure := self._place(run, INVOCATIONS_SCHEMA, rows)) is not None:
                return failure
            position = UsagePosition(usage[-1].fact.recorded_at, usage[-1].usage_id)

    # --- events -------------------------------------------------------------------------

    def _events_window(self, run: _Run) -> EgressFailure | None:
        """Pages the epochs with a lease minted in the window. A runner step starts at its epoch's first mint, so
        every step that started in the window is among them; rows of a step that started earlier are dropped."""
        assert self._paths is not None  # checked at construction
        after: EpochKey | None = None
        while True:
            epochs = self._event_reads.epochs_minted_between(run.since, run.until, after, self._batch_limit)
            if not epochs:
                return None
            markers = self._event_reads.epoch_markers(epochs, extractor_version=self._extractor_version)
            drops = self._event_reads.epoch_drops(epochs)
            derivations = {
                (held.marker.segment_id, held.marker.extractor_version): held
                for held in self._event_reads.derivations(markers)
            }
            facts = self._steps.step_facts_for(sorted({epoch.chunk_id for epoch in epochs}))
            items = sorted([*markers, *drops], key=position_of)
            rows = [
                entry
                for entry in events_rows(items, derivations, facts, self._paths, run.now)
                if run.since <= _step_started_at(entry[1]) < run.until
            ]
            if rows and (failure := self._place(run, EVENTS_SCHEMA, rows, EXTRACTOR_VERSION)) is not None:
                return failure
            after = epochs[-1]

    # --- placing ------------------------------------------------------------------------

    def _place(
        self, run: _Run, schema: DatasetSchema, rows: _Rows, extractor_version: str | None = None
    ) -> EgressFailure | None:
        """One page is one backfill pass with its own manifest; a dry run only counts the files it would place."""
        by_partition: dict[date, list[EgressValues]] = defaultdict(list)
        for partition, row in rows:
            by_partition[partition].append(row)
        tally = run.tallies[schema.name]
        if run.writer is None:
            tally.rows += len(rows)
            tally.files += files_needed([len(part) for part in by_partition.values()], self._max_rows_per_file)
            return None
        egress_pass = EgressPass(started_at=run.now, backfill=True, extractor_version=extractor_version)
        placed: list[PlacedFile] = []
        for partition in sorted(by_partition):
            written = guarded(
                lambda partition=partition: run.writer.write(  # type: ignore[union-attr, misc]
                    EgressBatch(schema, partition, egress_pass, by_partition[partition])
                )
            )
            if isinstance(written, EgressFailure):
                return written
            placed.extend(written.files)
        committed = guarded(lambda: run.writer.commit_pass(egress_pass, placed))  # type: ignore[union-attr]
        if isinstance(committed, EgressFailure):
            return committed
        tally.rows += len(rows)
        tally.files += len(placed)
        return None


def _step_started_at(row: EgressValues) -> datetime:
    value = row.values["step_started_at"]
    assert isinstance(value, datetime)
    return value
