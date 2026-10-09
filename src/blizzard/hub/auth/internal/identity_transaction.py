"""SQLAlchemy adapter for the identity unit of work (package-private).

:meth:`IdentityTransactions.begin` opens one write transaction, takes the identity lock as its first statement, and
builds every identity repository over connections pinned to it: their ``read`` and ``write`` hand back that one
connection rather than opening their own, so nothing commits until the unit of work does. Every read a repository makes
follows the lock, so a check the unit reads — the hub administrators, a tenant's admins — still holds when its writes
commit. A tenant-owned write joins the same transaction through :meth:`_TransactionConnections.scoped` — handed out as
``IdentityWrites.tenant_connections`` — whose connections are that tenant's builders over the one connection, opened
under the tenant's lock as any scoped write is.

The identity lock is a no-op ``UPDATE`` of the store's revision row (``bzh:sql-portable``): the one hub-level row every
store the hub serves holds, since the hub refuses to start on a store whose revision is not at head, and no identity
table is guaranteed a row. On SQLite the ``UPDATE`` takes the single writer lock before any read; on Postgres it
queues every other unit of work behind this one's commit, and each later statement reads what that commit wrote."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import sqlalchemy as sa
from sqlalchemy import Connection

from blizzard.foundation.logging import get_logger
from blizzard.hub.auth.errors import RepoErrorFactory
from blizzard.hub.auth.identity_transaction import IdentityWrites, IIdentityTransactions
from blizzard.hub.auth.internal.auth_facts_repository import AuthFactsRepository
from blizzard.hub.auth.internal.hub_admin_repository import HubAdminRepository
from blizzard.hub.auth.internal.identity_repository import IdentityRepository
from blizzard.hub.auth.internal.membership_repository import MembershipRepository
from blizzard.hub.auth.internal.session_repository import SessionRepository
from blizzard.hub.auth.internal.superuser_bootstrap_repository import SuperuserBootstrapRepository
from blizzard.hub.auth.internal.user_repository import UserRepository
from blizzard.hub.store.errors import HubStoreConnections, HubStoreErrorFactory
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.tenancy import StoreScope

#: The store's revision table, named only as the identity lock's target; nothing reads it here.
_REVISION_TABLE = sa.table("alembic_version", sa.column("version_num"))


class IdentityLockMissing(RuntimeError):
    """Raised when the store holds no revision row to lock: it was never migrated, so no unit of work may run on it."""

    def __init__(self) -> None:
        super().__init__("the hub store holds no revision row to lock; migrate it before writing identities")


def _lock_identities(conn: Connection) -> None:
    """Take the identity lock as the transaction's first statement; every unit of work takes the same one row, so
    two units never interleave and none waits on another in a different order."""
    locked = conn.execute(sa.update(_REVISION_TABLE).values(version_num=_REVISION_TABLE.c.version_num))
    if locked.rowcount == 0:
        raise IdentityLockMissing


class _TransactionConnections(HubStoreConnections):
    """Hub-level connections pinned to one open transaction. A fault inside it propagates to the unit of work's own
    ``write``, the one site that wraps it and rolls the transaction back."""

    def __init__(self, conn: Connection, errors: HubStoreErrorFactory) -> None:
        super().__init__(conn.engine, errors)
        self._conn = conn

    @contextmanager
    def read(self, operation: str) -> Iterator[Connection]:
        yield self._conn

    @contextmanager
    def write(self, operation: str, *, expect: tuple[type[BaseException], ...] = ()) -> Iterator[Connection]:
        yield self._conn

    def scoped(self, scope: StoreScope) -> ScopedConnections:
        """``scope``'s connections over this same transaction."""
        return _ScopedTransactionConnections(self._conn, self._errors, scope)


class _ScopedTransactionConnections(ScopedConnections, _TransactionConnections):
    """One tenant's connections pinned to the open transaction: :class:`ScopedConnections` binds the scope and takes
    the tenant's lock on the connection :class:`_TransactionConnections` hands it."""

    def __init__(self, conn: Connection, errors: HubStoreErrorFactory, scope: StoreScope) -> None:
        ScopedConnections.__init__(self, conn.engine, errors, scope)
        self._conn = conn


class IdentityTransactions:
    """Opens identity units of work over the hub store."""

    def __init__(self, store: HubStoreConnections, errors: RepoErrorFactory) -> None:
        self._store = store
        self._errors = errors
        self._store_errors = HubStoreErrorFactory(get_logger("blizzard.hub.store"))

    @contextmanager
    def begin(self) -> Iterator[IdentityWrites]:
        with self._store.write("identity_transaction") as conn:
            _lock_identities(conn)
            yield self._writes(_TransactionConnections(conn, self._store_errors))

    def _writes(self, connections: _TransactionConnections) -> IdentityWrites:
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


def _conforms_identity_transactions(x: IdentityTransactions) -> IIdentityTransactions:
    return x
