"""Tenant deletion — close, drain, complete (``blizzard-product:/plans/multi-tenancy/hub/spec/administration.md``
§Deleting a tenant).

Each phase commits on its own. **Close** records the tenant's deletion fact; from that commit on the tenant does not
resolve, its writes are refused, and what the hub holds in memory for it is dropped. **Drain** removes the tenant's
rows, children before parents, at most :data:`DRAIN_BATCH_ROWS` per transaction, so another tenant's writer waits at
most one batch. **Complete** removes the tenant itself and records when its deletion finished and how many rows it
removed.

A delete request drains inline under :data:`DRAIN_PASS_ROWS`; when that runs out first, the ``tenant_teardown`` sweep
(:class:`TenantTeardownSweep`) carries on, one bounded pass per tenant per interval (``bzh:steppable-loop``), from
wherever the tables stand — a hub killed mid-drain finishes the job after restart. The concrete store lives at
``src/blizzard/hub/store/internal/tenant_teardown_store.py`` (``bzh:dependency-inversion``)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.foundation.tenants import TenantDeletionState
from blizzard.hub.domain.observability.hub_events import HubEventKind
from blizzard.hub.tenancy import TenantId

#: The most rows one drain transaction removes.
DRAIN_BATCH_ROWS = 5_000

#: The most rows one drain pass removes — a delete request's inline drain, and each sweep pass over one tenant.
DRAIN_PASS_ROWS = 50_000

_log = get_logger("blizzard.hub.tenant_teardown")

# A kill at any of these leaves the deletion fact standing, so the ``tenant_teardown`` sweep resumes the drain from
# wherever the tables stand once the hub is back (``bzh:crash-point-registry``).
_CP_AFTER_CLOSE = crashpoint(
    "teardown.after-close.before-drain", "deletion fact committed and the tenant dropped; no row drained yet"
)
_CP_AFTER_BATCH = crashpoint(
    "teardown.after-batch.before-next", "a full drain batch and its count committed; the next batch not begun"
)
_CP_AFTER_DRAIN = crashpoint(
    "teardown.after-drain.before-complete", "every row of the tenant drained; the tenant itself not yet removed"
)


@domain_model
@dataclass(frozen=True)
class TenantDeletion:
    """A tenant's deletion fact: who closed it and when, how many rows its drain has removed so far, and when its
    deletion completed — ``None`` while it is underway."""

    tenant_id: TenantId
    name: str
    closed_at: datetime
    deleted_by: str
    rows_removed: int
    deleted_at: datetime | None


class CloseOutcome(StrEnum):
    """What asking to close a tenant found: it closed now, its deletion was already underway, or already done."""

    CLOSED = "closed"
    UNDERWAY = "underway"
    DELETED = "deleted"


class LastTenant(Exception):
    """Raised when the tenant asked to be deleted is the hub's only open tenant; nothing is closed."""

    def __init__(self, tenant: str) -> None:
        super().__init__(f"tenant {tenant} is the hub's only open tenant")
        self.tenant = tenant


class TenantAlreadyDeleted(LookupError):
    """Raised when the tenant asked to be deleted was deleted already; its deletion fact answers."""

    def __init__(self, deletion: TenantDeletion) -> None:
        super().__init__(f"tenant {deletion.tenant_id} was deleted")
        self.deletion = deletion


class IReadTenantTeardowns(Protocol):
    """The hub's deletion facts, read hub-wide."""

    def underway(self) -> list[TenantId]:
        """Every tenant whose deletion is underway, oldest deletion first."""
        ...

    def deletion(self, tenant: TenantId) -> TenantDeletion | None:
        """``tenant``'s deletion fact, or ``None`` when it was never closed."""
        ...


class IWriteTenantTeardowns(IReadTenantTeardowns, Protocol):
    """Adds the three phases' writes, each its own transaction."""

    def close(self, tenant: TenantId, *, by: str, at: datetime) -> CloseOutcome:
        """Lock ``tenant``'s row, then — unless a deletion fact already names it — record one with ``closed_at``.
        :class:`~blizzard.hub.tenancy.UnknownTenant` when no tenant has the id; :class:`LastTenant` when no other
        open tenant would remain."""
        ...

    def drain_batch(self, tenant: TenantId, limit: int) -> int | None:
        """Remove at most ``limit`` of ``tenant``'s rows in one transaction, walking its tables children first from
        the top, and add them to its deletion's count; the rows removed, or ``None`` when no deletion of ``tenant``
        is underway."""
        ...

    def complete(self, tenant: TenantId, *, at: datetime) -> int | None:
        """Delete ``tenant``'s ``tenants`` row and memberships and record ``deleted_at``; the rows its deletion
        removed in all, or ``None`` when no deletion of ``tenant`` is underway."""
        ...


class IRecordHubEvents(Protocol):
    """Appends one row to the hub's event log."""

    def record(self, *, kind: HubEventKind, message: str, detail: dict[str, object] | None, at: datetime) -> object:
        """Record a ``kind`` event at ``at``."""
        ...


@domain_model
@dataclass(frozen=True)
class DeletionProgress:
    """Where a delete left a tenant: deleted, with every row it removed, or still deleting."""

    state: TenantDeletionState
    rows_removed: int | None


class TenantTeardown:
    """Deletes tenants: closes one, drains it under a budget, and completes it.

    ``closed`` is told after a close commits, with the tenant's id, so the hub drops what it holds in memory for it."""

    def __init__(
        self,
        *,
        teardowns: IWriteTenantTeardowns,
        closed: Callable[[TenantId], object],
        events: IRecordHubEvents,
        clock: IClock,
        batch_rows: int = DRAIN_BATCH_ROWS,
    ) -> None:
        self._teardowns = teardowns
        self._closed = closed
        self._events = events
        self._clock = clock
        self._batch_rows = batch_rows

    def delete(self, tenant: TenantId, *, by: str, budget: int | None = None) -> DeletionProgress:
        """Close ``tenant`` and drain it inline under ``budget`` rows (:data:`DRAIN_PASS_ROWS` unless named),
        completing it when the drain finishes inside the budget. A deletion already underway is left to the sweep,
        untouched; one already completed raises :class:`TenantAlreadyDeleted`."""
        outcome = self.close(tenant, by=by)
        if outcome is CloseOutcome.UNDERWAY:
            return DeletionProgress(TenantDeletionState.DELETING, None)
        if outcome is CloseOutcome.DELETED:
            deletion = self._teardowns.deletion(tenant)
            assert deletion is not None
            raise TenantAlreadyDeleted(deletion)
        _CP_AFTER_CLOSE.reached()
        return self.advance(tenant, budget=DRAIN_PASS_ROWS if budget is None else budget)

    def close(self, tenant: TenantId, *, by: str) -> CloseOutcome:
        """Close ``tenant``: record its deletion fact, then drop what the hub holds in memory for it and record
        ``tenant-closed``. A tenant already closed is left as it is."""
        at = self._clock.now()
        outcome = self._teardowns.close(tenant, by=by, at=at)
        if outcome is CloseOutcome.CLOSED:
            self._closed(tenant)
            self._events.record(
                kind="tenant-closed", message=f"tenant {tenant} closed", detail={"tenant_id": tenant, "by": by}, at=at
            )
        return outcome

    def drain(self, tenant: TenantId, *, budget: int) -> tuple[int, bool]:
        """Drain ``tenant`` under ``budget`` rows, one batch per transaction; the rows removed, and whether the
        tenant holds none any more — a batch that removed fewer rows than it asked for walked every table. A drain
        that finds the deletion completed under it has nothing left to do."""
        removed = 0
        while removed < budget:
            limit = min(self._batch_rows, budget - removed)
            batch = self._teardowns.drain_batch(tenant, limit)
            if batch is None:
                return removed, True
            removed += batch
            if batch < limit:
                return removed, True
            _CP_AFTER_BATCH.reached()
        return removed, False

    def complete(self, tenant: TenantId) -> int | None:
        """Complete a drained ``tenant``'s deletion and record ``tenant-deleted``; the rows it removed in all, or
        ``None`` when its deletion was completed already."""
        at = self._clock.now()
        rows_removed = self._teardowns.complete(tenant, at=at)
        if rows_removed is not None:
            self._events.record(
                kind="tenant-deleted",
                message=f"tenant {tenant} deleted",
                detail={"tenant_id": tenant, "rows_removed": rows_removed},
                at=at,
            )
        return rows_removed

    def advance(self, tenant: TenantId, *, budget: int) -> DeletionProgress:
        """One drain pass over a closed ``tenant`` under ``budget`` rows, completing it when the pass finishes."""
        _, drained = self.drain(tenant, budget=budget)
        if not drained:
            return DeletionProgress(TenantDeletionState.DELETING, None)
        _CP_AFTER_DRAIN.reached()
        rows_removed = self.complete(tenant)
        if rows_removed is None:
            deletion = self._teardowns.deletion(tenant)
            rows_removed = deletion.rows_removed if deletion is not None else None
        return DeletionProgress(TenantDeletionState.DELETED, rows_removed)


class TenantTeardownSweep:
    """The ``tenant_teardown`` sweep: each pass advances every deletion underway by one bounded drain pass. One
    tenant's failure is logged and leaves the others' passes to run."""

    def __init__(self, *, teardown: TenantTeardown, teardowns: IReadTenantTeardowns, budget: int | None = None) -> None:
        self._teardown = teardown
        self._teardowns = teardowns
        self._budget = budget

    def sweep(self) -> None:
        for tenant in self._teardowns.underway():
            try:
                self._teardown.advance(tenant, budget=DRAIN_PASS_ROWS if self._budget is None else self._budget)
            except Exception as exc:
                _log.warning("tenant teardown pass failed", tenant=tenant, fault=repr(exc))


__all__ = [
    "DRAIN_BATCH_ROWS",
    "DRAIN_PASS_ROWS",
    "CloseOutcome",
    "DeletionProgress",
    "IReadTenantTeardowns",
    "IRecordHubEvents",
    "IWriteTenantTeardowns",
    "LastTenant",
    "TenantAlreadyDeleted",
    "TenantDeletion",
    "TenantTeardown",
    "TenantTeardownSweep",
]
