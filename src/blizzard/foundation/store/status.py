"""The store-status read seam (``bzh:dependency-inversion``, ``bzh:repository-split``).

A read-only seam over a daemon's store: can it be reached, and at what Alembic
revision does it sit? Operational, never a fleet fact. The Protocol is
dependency-free so its dependents stay free of the ORM (``bzh:domain-core``); the
adapter under ``internal/`` is the only place the engine is touched."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class StoreStatus:
    """A point-in-time reading of a store's reachability and applied revision.

    ``reachable`` is a *value*, not an exception: an unopenable store reports
    ``reachable=False`` with a ``detail`` rather than raising."""

    reachable: bool
    revision: str | None
    detail: str = ""


class IStoreStatusReader(Protocol):
    """Read-only store-status seam."""

    def read_status(self) -> StoreStatus: ...
