"""Reset: moves one dataset's cursor to an instant the operator names, and records the moved window.

Contract: ``blizzard-product:/plans/fact-egress/steps/spec/export.md`` §Operator surface and §Delivery semantics.
The move is one appended cursor row, written under the lock the sweep holds for a whole pass: a pass reads its
cursor when it starts and appends the advanced one when it ends, so a row appended mid-pass would be overwritten."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from blizzard.foundation.clock import IClock
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.config import EgressConfig
from blizzard.hub.domain.egress.repository import EgressCursorRecord, IWriteEgressCursor, UsagePosition
from blizzard.hub.domain.egress.schema import STEPS_SCHEMA
from blizzard.hub.domain.event_log import EventLogService
from blizzard.hub.domain.tracing.cursor import CursorKey


class ResetRefused(ValueError):
    """The dataset is not configured, or the instant is in the future."""


class ResetUnavailable(Exception):
    """The export is off or rejected, so there is no cursor to move."""


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
        pass_lock: threading.Lock,
    ) -> None:
        self._egress = egress
        self._events = events
        self._clock = clock
        self._datasets = config.datasets
        self._settle = timedelta(seconds=config.settle_seconds)
        self._active = active
        self._lock = pass_lock

    def reset(self, dataset: str, to: datetime) -> ResetResult:
        if not self._active:
            raise ResetUnavailable("the egress export is not configured; there is no cursor to reset")
        if dataset not in self._datasets:
            raise ResetRefused(f"dataset {dataset!r} is not configured; the export writes {', '.join(self._datasets)}")
        now = self._clock.now()
        if to > now:
            raise ResetRefused("to must not be in the future")
        with self._lock:
            cursor = self._egress.newest_cursor(dataset)
            previous = _position_at(cursor)
            # The first pass would anchor at now less the settle window, so an unanchored dataset moves from there.
            standing = previous if previous is not None else now - self._settle
            self._egress.append_cursor(
                EgressCursorRecord(
                    dataset,
                    CursorKey.opening(to) if dataset == STEPS_SCHEMA.name else None,
                    UsagePosition(to),
                    0,
                    (),
                    now,
                )
            )
        direction: Literal["skipped", "repeated"] = "skipped" if to > standing else "repeated"
        self._events.record(
            kind="egress-cursor-reset",
            runner_id=None,
            chunk_id=None,
            lease_id=None,
            node_name=None,
            message=f"fact egress {dataset} cursor reset; the window was {direction}",
            detail={
                "dataset": dataset,
                "from": iso_utc(previous) if previous is not None else None,
                "to": iso_utc(to),
                "direction": direction,
            },
            at=now,
        )
        return ResetResult(dataset, previous, to, direction)


def _position_at(cursor: EgressCursorRecord | None) -> datetime | None:
    if cursor is None:
        return None
    return cursor.step.at if cursor.step is not None else cursor.usage.recorded_at
