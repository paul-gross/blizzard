"""The row shapes a usage-limit mark and a floor clear share across their host and runner tables (package-private):
the decode, the encode, the invocation that makes a mark unique, and the retention window both adapters prune to."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import Row, Table, and_

from blizzard.foundation.store.utc import as_utc
from blizzard.runner.throttle.usage_limits import FloorClear, UsageLimitMark

# See IWriteSubscriptionUsageLimitRepository.prune_usage_limits's own docstring for the retention contract: a week of
# marks outlives any window a floor holds on, and a day more keeps the newest week whole.
USAGE_LIMIT_RETENTION_WINDOW = timedelta(days=8)


def mark_of(row: Row[Any]) -> UsageLimitMark:
    return UsageLimitMark(
        marked_at=as_utc(row.marked_at),
        resets_at=as_utc(row.resets_at) if row.resets_at is not None else None,
        window=row.window,
        window_resets_at=as_utc(row.window_resets_at) if row.window_resets_at is not None else None,
        harness_id=str(row.harness_id),
        detail=str(row.detail),
        lease_id=str(row.lease_id),
        epoch=int(row.epoch),
        generation=int(row.generation),
        invocation_kind=row.invocation_kind,
    )


def clear_of(row: Row[Any]) -> FloorClear:
    return FloorClear(cleared_at=as_utc(row.cleared_at), by=str(row.by))


def mark_values(mark: UsageLimitMark) -> dict[str, object]:
    return {
        "marked_at": mark.marked_at,
        "resets_at": mark.resets_at,
        "window": mark.window,
        "window_resets_at": mark.window_resets_at,
        "harness_id": mark.harness_id,
        "detail": mark.detail,
        "lease_id": mark.lease_id,
        "epoch": mark.epoch,
        "generation": mark.generation,
        "invocation_kind": mark.invocation_kind,
    }


def invocation_of(table: Table, mark: UsageLimitMark) -> Any:
    """The rows naming ``mark``'s invocation."""
    return and_(
        table.c.lease_id == mark.lease_id,
        table.c.epoch == mark.epoch,
        table.c.generation == mark.generation,
        table.c.invocation_kind == mark.invocation_kind,
    )
