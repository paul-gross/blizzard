"""A real hub unit holding findings' keys, for a test driving a guarded finding write or a garden delivery directly."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from blizzard.hub.domain.garden.delivery.materialize import DeliveryPlan
from blizzard.hub.domain.garden.findings.model import finding_lock
from blizzard.hub.domain.kernel.transactions import LockedTransaction
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.store.transactions import HubTransactions


@contextmanager
def finding_unit(connections: ScopedConnections, *finding_ids: str) -> Generator[LockedTransaction]:
    """One committed unit declaring every :func:`finding_lock` of ``finding_ids``, sorted."""
    with HubTransactions(connections).begin() as unit:
        yield unit.lock_set(keys=[finding_lock(finding_id) for finding_id in sorted(finding_ids)])


@contextmanager
def delivery_unit(connections: ScopedConnections, plan: DeliveryPlan) -> Generator[LockedTransaction]:
    """The unit a garden delivery declares: ``plan``'s chunk, then every expected finding's :func:`finding_lock`."""
    with HubTransactions(connections).begin() as unit:
        yield unit.lock_set(chunks=[plan.chunk_id], keys=[finding_lock(fid) for fid in sorted(plan.expect)])
