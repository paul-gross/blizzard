"""The runner-store composition root (blizzard#410, D4).

The only module under ``src/`` that names a concrete ``runner/store/internal/`` adapter
(the structural gate Phase 4 adds asserts this) — every other collaborator takes a
Protocol seam or the :class:`~blizzard.runner.stores.RunnerStores` bundle this builds.
Mirrors :func:`blizzard.hub.composition.build_services`."""

from __future__ import annotations

from sqlalchemy import Engine

from blizzard.runner.store.errors import RunnerStoreErrorFactory
from blizzard.runner.store.internal.base import RunnerStoreConnections
from blizzard.runner.store.internal.lease_store import LeaseStore
from blizzard.runner.stores import RunnerStores


def build_stores(engine: Engine, *, errors: RunnerStoreErrorFactory) -> RunnerStores:
    """Construct and wire every extracted concept-store adapter over a migrated engine."""
    connections = RunnerStoreConnections(engine, errors)
    return RunnerStores(leases=LeaseStore(connections))
