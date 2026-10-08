"""The egress export sweep: writes closed steps, usage, and event derivations and drops, in cursor order, as files.

Contract: ``blizzard-product:/delivered/fact-egress/steps/spec/export.md`` §What a pass does, §Late usage and §Delivery
semantics, and ``blizzard-product:/delivered/fact-egress/events/spec/export.md``. Every collaborator is injected, so
:meth:`EgressSweep.sweep` is one complete, directly-callable pass (``bzh:steppable-loop``). A dataset's cursor moves
only after its files and manifest are placed, so a crash before the cursor row re-writes the same rows next pass."""

from __future__ import annotations

# The export pass lock — debt, blizzard-context:/architecture/system-shape/exclusive-writes.md
# ast-grep-ignore: bzh:store-exclusive-write
import threading
from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, timedelta

from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.foundation.event_log import EventLogKind
from blizzard.foundation.lane_retry import OutageLatch
from blizzard.foundation.logging import get_logger
from blizzard.hub.domain.chunk.event_log import EventLogService
from blizzard.hub.domain.observability.analytics.events import DropFact
from blizzard.hub.domain.observability.analytics.extraction import EXTRACTOR_VERSION
from blizzard.hub.domain.observability.egress.assembly import (
    anchor_records,
    guarded,
    late_chunks,
    plan_invocations_pass,
    plan_steps_pass,
)
from blizzard.hub.domain.observability.egress.config import EgressConfig
from blizzard.hub.domain.observability.egress.event_rows import FilePathPolicy
from blizzard.hub.domain.observability.egress.events_window import events_rows, position_of, take
from blizzard.hub.domain.observability.egress.repository import (
    EgressCheckpoint,
    IReadEgressEvents,
    IWriteEgressCursor,
)
from blizzard.hub.domain.observability.egress.schema import (
    EVENTS_SCHEMA,
    INVOCATIONS_SCHEMA,
    STEPS_SCHEMA,
)
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

_log = get_logger("blizzard.hub.egress")

# Files placed, manifest not: the next pass re-reads from the unmoved cursor and writes new files.
_CP_EGRESS_AFTER_WRITE_BEFORE_COMMIT = crashpoint(
    "egress.after-write.before-commit",
    "the pass's files are placed; the manifest that lists them is not yet written",
)
# Manifest committed, cursor row not: recovered the same way.
_CP_EGRESS_AFTER_COMMIT_BEFORE_CURSOR = crashpoint(
    "egress.after-commit.before-cursor",
    "the pass's files and manifest are placed; the cursor row that records them is not yet appended",
)

_FAILED: EventLogKind = "egress-write-failed"
_RECOVERED: EventLogKind = "egress-write-recovered"

_Rows = list[tuple[date, EgressValues]]


class EgressSweep:
    """Select, assemble, write, commit, advance."""

    def __init__(
        self,
        *,
        steps: IReadTraceSteps,
        egress: IWriteEgressCursor,
        event_reads: IReadEgressEvents,
        paths: FilePathPolicy | None,
        writer: IEgressWriter,
        events: EventLogService,
        clock: IClock,
        config: EgressConfig,
        pass_lock: threading.Lock | None = None,
    ) -> None:
        if EVENTS_SCHEMA.name in config.datasets and paths is None:
            raise ValueError("the events dataset needs a file path policy")
        self._steps = steps
        self._egress = egress
        self._event_reads = event_reads
        self._paths = paths
        self._extractor_version = EXTRACTOR_VERSION if config.extractor_versions == "current" else None
        self._writer = writer
        self._events = events
        self._clock = clock
        self._datasets = config.datasets
        self._settle = timedelta(seconds=config.settle_seconds)
        self._batch_limit = config.batch_limit
        # A restart mid-outage must not announce the same failure again.
        self._latch = OutageLatch(
            timedelta(seconds=config.sweep_seconds), lambda: egress.newest_egress_latch() == _FAILED
        )
        self._pass_lock = pass_lock or threading.Lock()

    def sweep(self) -> None:
        """One pass, or none: while an operator's reset holds the pass lock the tick is skipped, not queued."""
        if not self._pass_lock.acquire(blocking=False):
            return
        try:
            self._pass()
        finally:
            self._pass_lock.release()

    def _pass(self) -> None:
        now = self._clock.now()
        if not self._latch.is_due(now):
            return
        cursors = {dataset: self._egress.newest_cursor(dataset) for dataset in self._datasets}
        anchors = anchor_records(cursors, now, self._settle)
        if anchors:
            for anchor in anchors:
                self._egress.append_cursor(anchor)
            return
        egress_pass = EgressPass(started_at=now)
        until = now - self._settle
        wrote = False
        for dataset in self._datasets:
            cursor = cursors[dataset]
            assert cursor is not None  # every dataset was anchored before this point
            outcome = self._dataset_pass(dataset, cursor, egress_pass, until)
            if isinstance(outcome, EgressFailure):
                self._failed(now, dataset, outcome)
                return
            wrote = wrote or outcome
        # A pass that placed nothing proves nothing about the directory.
        if wrote:
            self._recovered()

    def _dataset_pass(
        self, dataset: str, cursor: EgressCheckpoint, egress_pass: EgressPass, until: datetime
    ) -> bool | EgressFailure:
        if dataset == STEPS_SCHEMA.name:
            return self._steps_pass(cursor, egress_pass, until)
        if dataset == INVOCATIONS_SCHEMA.name:
            return self._invocations_pass(cursor, egress_pass, until)
        if dataset == EVENTS_SCHEMA.name:
            return self._events_pass(cursor, egress_pass, until)
        raise ValueError(f"no egress pass for dataset {dataset!r}")

    # --- steps --------------------------------------------------------------------------

    def _steps_pass(self, cursor: EgressCheckpoint, egress_pass: EgressPass, until: datetime) -> bool | EgressFailure:
        assert cursor.step is not None  # a steps cursor always carries its step position
        window = read_window(self._steps, cursor.step, until, self._batch_limit)
        usage = self._egress.usage_after(cursor.usage, until, self._batch_limit)
        late = self._steps.step_facts_for(late_chunks(window, usage))
        plan = plan_steps_pass(cursor, window, usage, late, egress_pass.started_at)
        if plan.rows:
            return self._write(STEPS_SCHEMA, plan.rows, egress_pass, plan.advanced)
        if plan.moved:
            self._egress.append_cursor(plan.advanced)
        return False

    # --- invocations --------------------------------------------------------------------

    def _invocations_pass(
        self, cursor: EgressCheckpoint, egress_pass: EgressPass, until: datetime
    ) -> bool | EgressFailure:
        usage = self._egress.usage_after(cursor.usage, until, self._batch_limit)
        if not usage:
            return False
        facts = self._steps.step_facts_for(sorted({row.chunk_id for row in usage}))
        page, advanced = plan_invocations_pass(usage, facts, egress_pass.started_at)
        for row in page.skipped:
            _log.warning("usage has no runner step; not exported", usage_id=row.usage_id, chunk_id=row.chunk_id)
        if page.rows:
            return self._write(INVOCATIONS_SCHEMA, page.rows, egress_pass, advanced)
        self._egress.append_cursor(advanced)
        return False

    # --- events -------------------------------------------------------------------------

    def _events_pass(self, cursor: EgressCheckpoint, egress_pass: EgressPass, until: datetime) -> bool | EgressFailure:
        assert cursor.events is not None  # an events cursor always carries its events position
        assert self._paths is not None  # checked at construction
        limit = self._batch_limit
        markers = self._event_reads.markers_after(
            cursor.events, until, limit, extractor_version=self._extractor_version
        )
        drops = self._event_reads.drops_after(cursor.events, until, limit)
        items = take(markers, drops, limit, read_limit=limit)
        if not items:
            return False
        derivations = {
            (held.marker.segment_id, held.marker.extractor_version): held
            for held in self._event_reads.derivations([item for item in items if not isinstance(item, DropFact)])
        }
        chunk_ids = sorted(
            {held.chunk_id for held in derivations.values()}
            | {item.chunk_id for item in items if isinstance(item, DropFact)}
        )
        facts = self._steps.step_facts_for(chunk_ids)
        rows = events_rows(items, derivations, facts, self._paths, egress_pass.started_at)
        advanced = EgressCheckpoint(
            EVENTS_SCHEMA.name, None, cursor.usage, len(rows), (), egress_pass.started_at, position_of(items[-1])
        )
        if rows:
            return self._write(EVENTS_SCHEMA, rows, replace(egress_pass, extractor_version=EXTRACTOR_VERSION), advanced)
        self._egress.append_cursor(advanced)
        return False

    # --- placing ------------------------------------------------------------------------

    def _write(
        self, schema: DatasetSchema, rows: _Rows, egress_pass: EgressPass, advanced: EgressCheckpoint
    ) -> bool | EgressFailure:
        """Place ``rows`` per date partition, commit the manifest, then append ``advanced``."""
        by_partition: dict[date, list[EgressValues]] = defaultdict(list)
        for partition, row in rows:
            by_partition[partition].append(row)
        placed: list[PlacedFile] = []
        for partition in sorted(by_partition):
            written = guarded(
                lambda partition=partition: self._writer.write(  # type: ignore[misc]
                    EgressBatch(schema, partition, egress_pass, by_partition[partition])
                )
            )
            if isinstance(written, EgressFailure):
                return written
            placed.extend(written.files)
        _CP_EGRESS_AFTER_WRITE_BEFORE_COMMIT.reached()
        committed = guarded(lambda: self._writer.commit_pass(egress_pass, placed))
        if isinstance(committed, EgressFailure):
            return committed
        _CP_EGRESS_AFTER_COMMIT_BEFORE_CURSOR.reached()
        files = (*(file.path for file in placed), committed.path)
        self._egress.append_cursor(replace(advanced, row_count=len(rows), files=files, recorded_at=self._clock.now()))
        _log.info("egress pass completed", dataset=schema.name, rows=len(rows), files=len(placed))
        return True

    # --- failure latch ------------------------------------------------------------------

    def _failed(self, now: datetime, dataset: str, failure: EgressFailure) -> None:
        opens = self._latch.failed(now)
        _log.warning(
            "egress write failed",
            dataset=dataset,
            cause=failure.cause.value,
            message=failure.message,
            failures=self._latch.failures,
            retry_in=self._latch.retry_in.total_seconds(),
        )
        if not opens:
            return
        detail: dict[str, object] = {"dataset": dataset, "cause": failure.cause.value, "message": failure.message}
        if failure.free_bytes is not None:
            detail["free_bytes"] = failure.free_bytes
            detail["required_bytes"] = failure.required_bytes
        self._record(_FAILED, "fact egress write failed; the cursor holds and the sweep retries with backoff", detail)

    def _recovered(self) -> None:
        if self._latch.succeeded():
            self._record(_RECOVERED, "fact egress recovered; held rows are being written", None)

    def _record(self, kind: EventLogKind, message: str, detail: dict | None) -> None:  # type: ignore[type-arg]
        self._events.record(
            kind=kind,
            runner_id=None,
            chunk_id=None,
            lease_id=None,
            node_name=None,
            message=message,
            detail=detail,
            at=self._clock.now(),
        )


__all__ = ["EgressSweep"]
