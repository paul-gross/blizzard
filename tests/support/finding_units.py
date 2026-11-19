"""A real hub unit holding findings' keys, for a test driving a guarded finding write or a garden delivery directly."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime

from blizzard.hub.domain.garden.delivery.materialize import DeliveryPlan
from blizzard.hub.domain.garden.findings.model import FactEntry, finding_lock
from blizzard.hub.domain.kernel.transactions import LockedTransaction
from blizzard.hub.store.internal.finding_store import FindingStore
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


def seed_fact(
    connections: ScopedConnections,
    finding_id: str,
    *,
    kind: str,
    at: datetime,
    note: str | None = None,
    actor: str | None = None,
    proposal_id: str | None = None,
    superseded_by: str | None = None,
) -> None:
    """Seed one fact on ``finding_id`` through the guarded write, in its own :func:`finding_unit`."""
    entry = FactEntry(
        finding_id=finding_id,
        kind=kind,
        at=at,
        note=note,
        actor=actor,
        proposal_id=proposal_id,
        superseded_by=superseded_by,
    )
    with finding_unit(connections, finding_id) as locked:
        FindingStore(connections).record_facts([entry], transaction=locked)
