"""SQLAlchemy adapter for the identity unit of work (package-private).

:meth:`IdentityTransactions.begin` opens one write transaction, takes the identity lock as its first statement, and
builds every identity repository over connections pinned to it: their ``read`` and ``write`` hand back that one
connection rather than opening their own, so nothing commits until the unit of work does, and every read a repository
makes follows the lock. A tenant-owned write joins the same transaction through :meth:`PinnedConnections.scoped` —
handed out as ``IdentityWrites.tenant_connections`` — whose connections are that tenant's builders over the one
connection, opened under the tenant's lock as any scoped write is.

The identity lock is the ``IDENTITY`` row of ``hub_locks``, taken by :func:`~blizzard.hub.store.locking.lock_hub`
(``bzh:store-exclusive-write``): a seeded row, where no identity table is guaranteed one
(``tests/component/hub/auth/test_identity_transaction.py::test_the_identity_lock_is_the_units_first_statement``). A unit
that also writes a tenant's lifecycle names ``hub=[HubLock.TENANT_LIFECYCLE]``; that lock follows ``IDENTITY`` before
any other statement. On SQLite the ``UPDATE`` takes the single writer lock before any read; on Postgres it queues every
other unit of work behind this one's commit, and each later statement reads what that commit wrote."""

from __future__ import annotations

from collections.abc import Generator, Sequence
from contextlib import contextmanager

from blizzard.hub.auth.errors import RepoErrorFactory
from blizzard.hub.auth.identity_transaction import IdentityWrites, IIdentityTransactions
from blizzard.hub.auth.internal.auth_facts_repository import AuthFactsRepository
from blizzard.hub.auth.internal.hub_admin_repository import HubAdminRepository
from blizzard.hub.auth.internal.identity_repository import IdentityRepository
from blizzard.hub.auth.internal.membership_repository import MembershipRepository
from blizzard.hub.auth.internal.session_repository import SessionRepository
from blizzard.hub.auth.internal.superuser_bootstrap_repository import SuperuserBootstrapRepository
from blizzard.hub.auth.internal.user_repository import UserRepository
from blizzard.hub.domain.kernel.locks import HubLock
from blizzard.hub.domain.kernel.transactions import TransactionMisuse
from blizzard.hub.store.borrowed import PinnedConnections
from blizzard.hub.store.errors import HubStoreConnections, HubStoreErrorFactory
from blizzard.hub.store.locking import lock_hub


class IdentityTransactions:
    """Opens identity units of work over the hub store."""

    def __init__(
        self, store: HubStoreConnections, errors: RepoErrorFactory, store_errors: HubStoreErrorFactory
    ) -> None:
        self._store = store
        self._errors = errors
        self._store_errors = store_errors

    @contextmanager
    def begin(self, *, hub: Sequence[HubLock] = ()) -> Generator[IdentityWrites, None, None]:
        named = sorted(set(hub))
        if any(lock is not HubLock.TENANT_LIFECYCLE for lock in named):
            raise TransactionMisuse("an identity unit may name only the tenant lifecycle lock")
        with self._store.write("identity_transaction") as conn:
            lock_hub(conn, HubLock.IDENTITY)
            for lock in named:
                lock_hub(conn, lock)
            yield self._writes(PinnedConnections(conn, self._store_errors))

    def _writes(self, connections: PinnedConnections) -> IdentityWrites:
        return IdentityWrites(
            users=UserRepository(connections, self._errors),
            identities=IdentityRepository(connections, self._errors),
            sessions=SessionRepository(connections, self._errors),
            memberships=MembershipRepository(connections),
            hub_admins=HubAdminRepository(connections),
            bootstrap=SuperuserBootstrapRepository(connections),
            auth_facts=AuthFactsRepository(connections),
            tenant_connections=connections.scoped,
        )


_identity_transactions_conforms: type[IIdentityTransactions] = IdentityTransactions
