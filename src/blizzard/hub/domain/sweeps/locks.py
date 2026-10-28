"""The in-memory locks a sweep pass holds: its sweep's across every tenant, or its sweep's in one tenant. They end
with the process."""

from __future__ import annotations

# The lock orders this process's own pass bookkeeping; it guards no store decision
# ast-grep-ignore: bzh:store-exclusive-write
import threading
from collections.abc import Generator, Set
from contextlib import contextmanager

from blizzard.hub.domain.sweeps.names import SweepName
from blizzard.hub.tenancy import TenantId

#: A held lock: a sweep across every tenant (``None``), or a sweep in one tenant.
SweepLock = tuple[SweepName, TenantId | None]


class SweepInProgress(Exception):
    """The lock a pass needs is held by another pass of the same sweep."""

    def __init__(self, name: SweepName) -> None:
        super().__init__(f"a {name} pass is in progress")
        self.name = name


def admits(held: Set[SweepLock], lock: SweepLock) -> bool:
    """Whether ``lock`` may be taken beside ``held``: a pass across every tenant needs no other pass of its sweep;
    a one-tenant pass needs no pass of its sweep across every tenant and none in its own tenant."""
    name, tenant = lock
    if tenant is None:
        return all(held_name != name for held_name, _ in held)
    return (name, None) not in held and lock not in held


class SweepLocks:
    """Every sweep lock held in this process, taken without blocking."""

    def __init__(self) -> None:
        self._mutex = threading.Lock()
        self._held: set[SweepLock] = set()

    @contextmanager
    def hold(self, name: SweepName, tenant: TenantId | None = None) -> Generator[None, None, None]:
        """Hold ``name``'s lock — in ``tenant`` alone, or across every tenant — for the block; raises
        :class:`SweepInProgress` at once when :func:`admits` refuses it."""
        lock: SweepLock = (name, tenant)
        with self._mutex:
            if not admits(self._held, lock):
                raise SweepInProgress(name)
            self._held.add(lock)
        try:
            yield
        finally:
            with self._mutex:
                self._held.discard(lock)
