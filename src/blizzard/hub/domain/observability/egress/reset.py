"""Reset: moves one dataset's cursor to an instant the operator names, and records the moved window.

Contract: ``blizzard-product:/plans/fact-egress/steps/spec/export.md`` §Operator surface and §Delivery semantics.
The move is one appended cursor row, written under the lock the sweep holds for a whole pass: a pass reads its
cursor when it starts and appends the advanced one when it ends, so a row appended mid-pass would be overwritten."""

from __future__ import annotations

# The export pass lock — debt, blizzard-context:/architecture/system-shape/exclusive-writes.md
# ast-grep-ignore: bzh:store-exclusive-write
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import dto
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.config import EgressConfig
from blizzard.hub.domain.chunk.event_log import EventLogService
from blizzard.hub.domain.observability.egress.event_rows import missing_key_reason
from blizzard.hub.domain.observability.egress.lifecycle import ExportVerb, export_allows
from blizzard.hub.domain.observability.egress.repository import (
    EgressCheckpoint,
    EventsPosition,
    IWriteEgressCursor,
    UsagePosition,
)
from blizzard.hub.domain.observability.egress.schema import EVENTS_SCHEMA, INVOCATIONS_SCHEMA, STEPS_SCHEMA
from blizzard.hub.domain.observability.tracing.cursor import CursorKey


class ResetRefused(ValueError):
    """The dataset is not configured, or the instant is in the future."""


class ResetUnavailable(Exception):
    """The export is off or rejected, so there is no cursor to move."""


@dto
@dataclass(frozen=True)
class ResetResult:
    """``previous`` is where the dataset stood, ``None`` before its first pass; ``direction`` says whether the
    window between was ``skipped`` (the cursor moved forward) or ``repeated`` (it moved back)."""

    dataset: str
    previous: datetime | None
    moved_to: datetime
    direction: Literal["skipped", "repeated"]


class EgressReset:
    def __init__(
        self,
        *,
        egress: IWriteEgressCursor,
        events: EventLogService,
        clock: IClock,
        config: EgressConfig,
        active: bool,
        missing_path_key: str | None = None,
        pass_lock: threading.Lock,
    ) -> None:
        self._egress = egress
        self._events = events
        self._clock = clock
        self._datasets = config.datasets
        self._settle = timedelta(seconds=config.settle_seconds)
        self._active = active
        self._missing_path_key = missing_path_key
        self._lock = pass_lock

    def reset(self, dataset: str, to: datetime) -> ResetResult:
        now = self._clock.now()
        require_resettable(
            dataset,
            to,
            active=self._active,
            datasets=self._datasets,
            now=now,
            missing_path_key=self._missing_path_key,
        )
        with self._lock:
            checkpoint, result = plan_reset(
                dataset, self._egress.newest_cursor(dataset), to, now=now, settle=self._settle
            )
            self._egress.append_cursor(checkpoint)
        self._events.record(
            kind="egress-cursor-reset",
            runner_id=None,
            chunk_id=None,
            lease_id=None,
            node_name=None,
            message=f"fact egress {dataset} cursor reset; the window was {result.direction}",
            detail={
                "dataset": dataset,
                "from": iso_utc(result.previous) if result.previous is not None else None,
                "to": iso_utc(to),
                "direction": result.direction,
            },
            at=now,
        )
        return result


def require_resettable(
    dataset: str,
    to: datetime,
    *,
    active: bool,
    datasets: Sequence[str],
    now: datetime,
    missing_path_key: str | None = None,
) -> None:
    """Refuse a reset the export cannot honour — the export off first, then the events dataset while
    its path hash key is missing, then a dataset it does not write, then a ``to`` in the future. Any
    past ``to`` is accepted, the standing position included."""
    if not export_allows(ExportVerb.RESET, wired=active):
        raise ResetUnavailable("the egress export is not configured; there is no cursor to reset")
    if dataset == EVENTS_SCHEMA.name and missing_path_key is not None:
        raise ResetRefused(missing_key_reason(missing_path_key))
    if dataset not in datasets:
        raise ResetRefused(f"dataset {dataset!r} is not configured; the export writes {', '.join(datasets)}")
    if to > now:
        raise ResetRefused("to must not be in the future")


def plan_reset(
    dataset: str, cursor: EgressCheckpoint | None, to: datetime, *, now: datetime, settle: timedelta
) -> tuple[EgressCheckpoint, ResetResult]:
    """The cursor row a reset to ``to`` appends, and what it did. An unanchored dataset stands
    where its first pass would anchor it — ``now`` less the settle window. The window is
    ``skipped`` when ``to`` lies past where the dataset stood and ``repeated`` otherwise, a reset
    to exactly the standing position included."""
    previous = cursor.position if cursor is not None else None
    standing = previous if previous is not None else now - settle
    direction: Literal["skipped", "repeated"] = "skipped" if to > standing else "repeated"
    return _moved(dataset, to, now), ResetResult(dataset, previous, to, direction)


def _moved(dataset: str, to: datetime, now: datetime) -> EgressCheckpoint:
    """``dataset``'s cursor at ``to``: before every fact at or after it, in that dataset's own order."""
    if dataset == STEPS_SCHEMA.name:
        return EgressCheckpoint(dataset, CursorKey.opening(to), UsagePosition(to), 0, (), now)
    if dataset == INVOCATIONS_SCHEMA.name:
        return EgressCheckpoint(dataset, None, UsagePosition(to), 0, (), now)
    if dataset == EVENTS_SCHEMA.name:
        return EgressCheckpoint(dataset, None, UsagePosition(to), 0, (), now, EventsPosition(to))
    raise ResetRefused(f"no cursor to reset for dataset {dataset!r}")
