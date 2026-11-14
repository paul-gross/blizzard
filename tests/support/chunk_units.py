"""A real hub unit holding chunk rows' locks, for a test driving a chunk-store write or read directly."""

from __future__ import annotations

from collections.abc import Generator, Sequence
from contextlib import contextmanager

from blizzard.hub.domain.kernel.locks import LockKey
from blizzard.hub.domain.kernel.transactions import LockedTransaction
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.store.transactions import HubTransactions


@contextmanager
def chunk_unit(
    connections: ScopedConnections, chunk_ids: Sequence[str], keys: Sequence[LockKey] = ()
) -> Generator[LockedTransaction]:
    """One committed unit declaring the chunk locks of ``chunk_ids`` and every key of ``keys``."""
    with HubTransactions(connections).begin() as unit:
        yield unit.lock_set(chunks=chunk_ids, keys=keys)
