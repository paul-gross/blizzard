"""A real hub unit holding garden proposals' keys, for a test driving a proposal or closure store write directly."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from blizzard.hub.domain.garden.proposals.model import garden_proposal_lock
from blizzard.hub.domain.kernel.transactions import LockedTransaction
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.store.transactions import HubTransactions


@contextmanager
def proposal_unit(connections: ScopedConnections, *proposal_ids: str) -> Generator[LockedTransaction]:
    """One committed unit declaring every :func:`garden_proposal_lock` of ``proposal_ids``."""
    with HubTransactions(connections).begin() as unit:
        yield unit.lock_set(keys=[garden_proposal_lock(proposal_id) for proposal_id in proposal_ids])
