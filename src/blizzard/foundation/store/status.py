"""The store-status read seam (``bzh:dependency-inversion``, ``bzh:repository-split``).

A read-only seam over a daemon's store: can the store be reached, and at what
Alembic revision does it sit? This is the substrate a daemon's *readiness* rule
is built on (each daemon's ``domain/readiness.py``) — operational, not business:
it reports connectivity and schema revision, never a fleet fact.

The Protocol is dependency-free (dataclass + ``Protocol`` only), so the domain
that depends on it stays free of the ORM (``bzh:domain-core``); the concrete
SQLAlchemy adapter lives under ``internal/`` and is the only place the engine is
touched (``bzh:pluggable-seams``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class StoreStatus:
    """A point-in-time reading of a store's reachability and applied revision.

    ``reachable`` is a *value*, not an exception: a store that cannot be opened
    reports ``reachable=False`` with a ``detail``, because "can I reach my store"
    is a readiness question whose negative answer is data, not a failure to raise.
    """

    reachable: bool
    revision: str | None
    detail: str = ""


class IStoreStatusReader(Protocol):
    """Read-only store-status seam. Held by readiness code at the edges and the domain."""

    def read_status(self) -> StoreStatus: ...
