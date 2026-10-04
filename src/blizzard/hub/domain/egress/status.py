"""The operator's read of the fact-egress export: on, off or rejected, each dataset's cursor and lag, the last
pass and file, the last error, and the free space.

Contract: ``blizzard-product:/plans/fact-egress/steps/spec/export.md`` §Operator surface. Everything here is a
fact the sweep left in the store or a setting parsed at start (``bzh:facts-not-status``), so it survives a restart
and needs no reach into the sweep. The clock and the free-space probe are injected; no write is possible."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from blizzard.foundation.clock import IClock
from blizzard.hub.config import EgressConfig
from blizzard.hub.domain.analytics.extraction import EXTRACTOR_VERSION
from blizzard.hub.domain.egress.repository import EgressCursorRecord, IReadEgress, IReadEgressEvents
from blizzard.hub.domain.egress.schema import EVENTS_SCHEMA, INVOCATIONS_SCHEMA, STEPS_SCHEMA
from blizzard.hub.domain.tracing.repository import IReadTraceSteps
from blizzard.hub.domain.tracing.window import oldest_unsent

EgressState = Literal["on", "off", "rejected"]
FreeSpaceProbe = Callable[[], int | None]
_FAILED = "egress-write-failed"


@dataclass(frozen=True)
class DatasetStatus:
    """``cursor_at`` is the dataset's position in time; ``lag_seconds`` the age of the oldest row past it,
    ``None`` when nothing waits."""

    name: str
    cursor_at: datetime | None
    lag_seconds: float | None


@dataclass(frozen=True)
class EgressStatus:
    """``last_pass_*`` is the newest cursor row of any dataset; ``last_file`` the last data file the newest
    writing pass placed, never its manifest. ``last_error_at`` is when the newest failure began — the sweep
    records only the first failure after a success — and ``last_error_ongoing`` whether no write has succeeded
    since. ``free_bytes`` is ``None`` when the directory cannot be read."""

    state: EgressState
    rejected_setting: str | None
    rejected_value: str | None
    directory: str | None
    format: str | None
    datasets: tuple[DatasetStatus, ...]
    last_pass_at: datetime | None
    last_pass_dataset: str | None
    last_pass_row_count: int | None
    last_file: str | None
    last_error_at: datetime | None
    last_error_message: str | None
    last_error_ongoing: bool
    free_bytes: int | None
    min_free_bytes: int | None
    backfill_max_window_seconds: int


class EgressStatusReader:
    def __init__(
        self,
        *,
        config: EgressConfig,
        rejected: bool,
        missing_path_key: str | None = None,
        egress: IReadEgress,
        event_reads: IReadEgressEvents,
        steps: IReadTraceSteps,
        clock: IClock,
        free_space: FreeSpaceProbe,
    ) -> None:
        self._config = config
        self._rejected = rejected
        self._missing_path_key = missing_path_key
        self._egress = egress
        self._event_reads = event_reads
        self._steps = steps
        self._clock = clock
        self._free_space = free_space

    def read(self) -> EgressStatus:
        config = self._config
        if config.directory is None:
            return self._idle("off", None, None)
        if self._rejected:
            return self._idle("rejected", "egress.format", config.format)
        cursors = {dataset: self._egress.newest_cursor(dataset) for dataset in config.datasets}
        passes = [cursor for cursor in cursors.values() if cursor is not None]
        last_pass = max(passes, key=lambda cursor: cursor.recorded_at, default=None)
        written = self._egress.newest_cursor_with_files()
        failure = self._egress.newest_egress_failure()
        ongoing = failure is not None and self._egress.newest_egress_latch() == _FAILED
        return EgressStatus(
            state="on",
            # A missing path hash key drops only the events dataset; the export stays on and names the variable.
            rejected_setting="egress.path_key_env" if self._missing_path_key is not None else None,
            rejected_value=self._missing_path_key,
            directory=str(config.directory),
            format=config.format,
            datasets=tuple(self._dataset(name, cursor) for name, cursor in cursors.items()),
            last_pass_at=last_pass.recorded_at if last_pass else None,
            last_pass_dataset=last_pass.dataset if last_pass else None,
            last_pass_row_count=last_pass.row_count if last_pass else None,
            last_file=_last_data_file(written),
            last_error_at=failure.at if failure else None,
            last_error_message=failure.message if failure else None,
            last_error_ongoing=ongoing,
            free_bytes=self._free_space(),
            min_free_bytes=config.min_free_bytes,
            backfill_max_window_seconds=config.backfill_max_window,
        )

    def _idle(self, state: EgressState, setting: str | None, value: str | None) -> EgressStatus:
        config = self._config
        return EgressStatus(
            state=state,
            rejected_setting=setting,
            rejected_value=value,
            directory=str(config.directory) if config.directory is not None else None,
            format=config.format if config.directory is not None else None,
            datasets=(),
            last_pass_at=None,
            last_pass_dataset=None,
            last_pass_row_count=None,
            last_file=None,
            last_error_at=None,
            last_error_message=None,
            last_error_ongoing=False,
            free_bytes=None,
            min_free_bytes=None,
            backfill_max_window_seconds=config.backfill_max_window,
        )

    def _dataset(self, name: str, cursor: EgressCursorRecord | None) -> DatasetStatus:
        if cursor is None:
            return DatasetStatus(name, None, None)
        now = self._clock.now()
        if name == STEPS_SCHEMA.name:
            assert cursor.step is not None  # a steps cursor always carries its step position
            oldest_step = oldest_unsent(self._steps, cursor.step, now)
            return DatasetStatus(name, cursor.step.at, _lag(now, oldest_step.key.at if oldest_step else None))
        if name == INVOCATIONS_SCHEMA.name:
            waiting = self._egress.usage_after(cursor.usage, now, 1)
            return DatasetStatus(
                name, cursor.usage.recorded_at, _lag(now, waiting[0].fact.recorded_at if waiting else None)
            )
        if name == EVENTS_SCHEMA.name:
            assert cursor.events is not None  # an events cursor always carries its events position
            return DatasetStatus(name, cursor.events.at, _lag(now, self._oldest_event(cursor, now)))
        raise ValueError(f"no egress status for dataset {name!r}")

    def _oldest_event(self, cursor: EgressCursorRecord, now: datetime) -> datetime | None:
        assert cursor.events is not None
        version = EXTRACTOR_VERSION if self._config.extractor_versions == "current" else None
        markers = self._event_reads.markers_after(cursor.events, now, 1, extractor_version=version)
        drops = self._event_reads.drops_after(cursor.events, now, 1)
        times = [*(m.derived_at for m in markers), *(d.dropped_at for d in drops)]
        return min(times, default=None)


def _lag(now: datetime, oldest: datetime | None) -> float | None:
    return max((now - oldest).total_seconds(), 0.0) if oldest is not None else None


def _last_data_file(record: EgressCursorRecord | None) -> str | None:
    """A cursor row's files end with its manifest; the file before it is the last data file."""
    if record is None or len(record.files) < 2:
        return None
    return record.files[-2]
