"""The egress export sweep: writes closed steps and usage, in cursor order, as immutable files.

Contract: ``blizzard-product:/plans/fact-egress/steps/spec/export.md`` §What a pass does, §Late usage and
§Delivery semantics. Every collaborator is injected, so :meth:`EgressSweep.sweep` is one complete,
directly-callable pass (``bzh:steppable-loop``). A dataset's cursor moves only after its files and its manifest are
placed, so a crash anywhere before the cursor row re-writes the same rows on the next pass."""

from __future__ import annotations

# The export pass lock — debt, blizzard-context:/architecture/system-shape/exclusive-writes.md
# ast-grep-ignore: bzh:store-exclusive-write
import threading
from collections import defaultdict
from datetime import date, datetime, timedelta

from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.foundation.event_log import EventLogKind
from blizzard.foundation.lane_retry import OutageLatch
from blizzard.foundation.logging import get_logger
from blizzard.hub.config import EgressConfig
from blizzard.hub.domain.egress.assembly import add_step, guarded, invocation_entry, runner_step, step_partition
from blizzard.hub.domain.egress.repository import EgressCursorRecord, IWriteEgressCursor, UsagePosition
from blizzard.hub.domain.egress.schema import (
    INVOCATIONS_SCHEMA,
    STEPS_SCHEMA,
)
from blizzard.hub.domain.event_log import EventLogService
from blizzard.hub.domain.tracing.cursor import CursorKey
from blizzard.hub.domain.tracing.facts import StepFacts
from blizzard.hub.domain.tracing.repository import IReadTraceSteps
from blizzard.hub.domain.tracing.steps import NodeStep, identify_steps
from blizzard.hub.domain.tracing.window import read_window
from blizzard.hub.egress.writer import (
    DatasetSchema,
    EgressBatch,
    EgressFailure,
    EgressPass,
    EgressRow,
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

_Rows = list[tuple[date, EgressRow]]


class EgressSweep:
    """Select, assemble, write, commit, advance."""

    def __init__(
        self,
        *,
        steps: IReadTraceSteps,
        egress: IWriteEgressCursor,
        writer: IEgressWriter,
        events: EventLogService,
        clock: IClock,
        config: EgressConfig,
        pass_lock: threading.Lock | None = None,
    ) -> None:
        self._steps = steps
        self._egress = egress
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
        if any(cursor is None for cursor in cursors.values()):
            self._anchor(cursors, now)
            return
        egress_pass = EgressPass(started_at=now)
        until = now - self._settle
        wrote = False
        for dataset in self._datasets:
            cursor = cursors[dataset]
            assert cursor is not None  # every dataset was anchored before this point
            outcome = (
                self._steps_pass(cursor, egress_pass, until)
                if dataset == STEPS_SCHEMA.name
                else self._invocations_pass(cursor, egress_pass, until)
            )
            if isinstance(outcome, EgressFailure):
                self._failed(now, dataset, outcome)
                return
            wrote = wrote or outcome
        # A pass that placed nothing proves nothing about the directory.
        if wrote:
            self._recovered()

    # --- the first pass -----------------------------------------------------------------

    def _anchor(self, cursors: dict[str, EgressCursorRecord | None], now: datetime) -> None:
        """Starts each unanchored dataset's export at ``now`` less the settle window, writing no rows."""
        at = now - self._settle
        for dataset, cursor in cursors.items():
            if cursor is not None:
                continue
            step = CursorKey.opening(at) if dataset == STEPS_SCHEMA.name else None
            self._egress.append_cursor(EgressCursorRecord(dataset, step, UsagePosition(at), 0, (), now))

    # --- steps --------------------------------------------------------------------------

    def _steps_pass(self, cursor: EgressCursorRecord, egress_pass: EgressPass, until: datetime) -> bool | EgressFailure:
        assert cursor.step is not None  # a steps cursor always carries its step position
        window = read_window(self._steps, cursor.step, until, self._batch_limit)
        usage = self._egress.usage_after(cursor.usage, until, self._batch_limit)
        now = egress_pass.started_at
        facts: dict[str, StepFacts] = {closed.step.key.chunk_id: closed.facts for closed in window.closed_steps()}
        steps: dict[str, tuple[NodeStep, ...]] = {
            closed.step.key.chunk_id: closed.steps for closed in window.closed_steps()
        }
        batch: dict[str, tuple[CursorKey, EgressRow]] = {}
        for closed in window.closed_steps():
            add_step(batch, closed.facts, closed.steps, closed.step, closed.key, now)
        late = [row for row in usage if row.chunk_id not in facts]
        for chunk_id, held in self._steps.step_facts_for(sorted({row.chunk_id for row in late})).items():
            facts[chunk_id] = held
            steps[chunk_id] = identify_steps(held)
        for row in usage:
            chunk = facts.get(row.chunk_id)
            step = runner_step(steps[row.chunk_id], row.fact.epoch) if chunk is not None else None
            if chunk is None or step is None or step.close is None:
                continue
            key = CursorKey.of(step)
            if key <= window.position:
                add_step(batch, chunk, steps[row.chunk_id], step, key, now)
        position = usage[-1] if usage else None
        advanced = EgressCursorRecord(
            STEPS_SCHEMA.name,
            window.position,
            UsagePosition(position.fact.recorded_at, position.usage_id) if position is not None else cursor.usage,
            len(batch),
            (),
            now,
        )
        rows = [(step_partition(row), row) for _, row in sorted(batch.values(), key=lambda entry: entry[0])]
        if rows:
            return self._write(STEPS_SCHEMA, rows, egress_pass, advanced)
        if advanced.step != cursor.step or advanced.usage != cursor.usage:
            self._egress.append_cursor(advanced)
        return False

    # --- invocations --------------------------------------------------------------------

    def _invocations_pass(
        self, cursor: EgressCursorRecord, egress_pass: EgressPass, until: datetime
    ) -> bool | EgressFailure:
        usage = self._egress.usage_after(cursor.usage, until, self._batch_limit)
        if not usage:
            return False
        facts = self._steps.step_facts_for(sorted({row.chunk_id for row in usage}))
        steps = {chunk_id: identify_steps(held) for chunk_id, held in facts.items()}
        rows: _Rows = []
        for row in usage:
            chunk = facts.get(row.chunk_id)
            entry = (
                invocation_entry(chunk, steps[row.chunk_id], row, egress_pass.started_at) if chunk is not None else None
            )
            if entry is None:
                _log.warning("usage has no runner step; not exported", usage_id=row.usage_id, chunk_id=row.chunk_id)
                continue
            rows.append(entry)
        last = usage[-1]
        advanced = EgressCursorRecord(
            INVOCATIONS_SCHEMA.name,
            None,
            UsagePosition(last.fact.recorded_at, last.usage_id),
            len(rows),
            (),
            egress_pass.started_at,
        )
        if rows:
            return self._write(INVOCATIONS_SCHEMA, rows, egress_pass, advanced)
        self._egress.append_cursor(advanced)
        return False

    # --- placing ------------------------------------------------------------------------

    def _write(
        self, schema: DatasetSchema, rows: _Rows, egress_pass: EgressPass, advanced: EgressCursorRecord
    ) -> bool | EgressFailure:
        """Place ``rows`` per date partition, commit the manifest, then append ``advanced``."""
        by_partition: dict[date, list[EgressRow]] = defaultdict(list)
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
        self._egress.append_cursor(
            EgressCursorRecord(advanced.dataset, advanced.step, advanced.usage, len(rows), files, self._clock.now())
        )
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
