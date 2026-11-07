"""The lease-locked unit seam — one write transaction, locked on the lease's own row, in which a declaration's
admission and an outcome's capture read and write the lease's facts without interleaving.

A declaration (an asset or a commit pointer) is accepted until the lease's outcome is buffered, and refused after. The
check and the insert on one side, and the harvest and the enqueue on the other, each run as one unit here, so no
declaration is admitted that no completion rides."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol

__all__ = ["ILeaseUnit", "IWriteLeaseLock"]


class ILeaseUnit(Protocol):
    """The opaque token of an open lease-locked unit. A repository method that takes one runs on the unit's
    transaction; the domain only passes it through, and the adapter alone knows what it carries."""


class IWriteLeaseLock(Protocol):
    """Opens a lease-locked unit — held only by the domain."""

    def locked(self, lease_id: str) -> AbstractContextManager[ILeaseUnit]:
        """One write transaction whose first statement locks ``lease_id``'s row: writers of one lease serialize on it,
        and other leases are untouched. Commits when the block leaves cleanly; rolls back when it raises."""
        ...
