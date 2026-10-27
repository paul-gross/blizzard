"""The store-status read seam (``bzh:dependency-inversion``, ``bzh:repository-split``): can the store be
reached, and at what Alembic revision does it sit? Operational, never a fleet fact."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from blizzard.foundation.roles import domain_model


@domain_model
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
