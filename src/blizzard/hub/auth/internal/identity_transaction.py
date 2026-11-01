"""SQLAlchemy adapter for the identity unit of work (package-private).

:meth:`IdentityTransactions.begin` opens one write transaction, takes the identity lock as its first statement, and
builds every identity repository over connections pinned to it: their ``read`` and ``write`` hand back that one
connection rather than opening their own, so nothing commits until the unit of work does, and every read a repository
makes follows the lock. A tenant-owned write joins the same transaction through :meth:`PinnedConnections.scoped` —
handed out as ``IdentityWrites.tenant_connections`` — whose connections are that tenant's builders over the one
connection, opened under the tenant's lock as any scoped write is.

The identity lock is a no-op ``UPDATE`` of the store's revision row (``bzh:sql-portable``): the one hub-level row every
store the hub serves holds, where no identity table is guaranteed a row
(``tests/test_identity_transaction.py::test_the_identity_lock_is_the_units_first_statement``). On SQLite the ``UPDATE``
takes the single writer lock before any read; on Postgres it queues every other unit of work behind this one's commit,
and each later statement reads what that commit wrote."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

import sqlalchemy as sa
from sqlalchemy import Connection

from blizzard.hub.auth.errors import RepoErrorFactory
from blizzard.hub.auth.identity_transaction import IdentityWrites, IIdentityTransactions
from blizzard.hub.auth.internal.auth_facts_repository import AuthFactsRepository
from blizzard.hub.auth.internal.hub_admin_repository import HubAdminRepository
from blizzard.hub.auth.internal.identity_repository import IdentityRepository
from blizzard.hub.auth.internal.membership_repository import MembershipRepository
from blizzard.hub.auth.internal.session_repository import SessionRepository
from blizzard.hub.auth.internal.superuser_bootstrap_repository import SuperuserBootstrapRepository
from blizzard.hub.auth.internal.user_repository import UserRepository
from blizzard.hub.store.borrowed import PinnedConnections
from blizzard.hub.store.errors import HubStoreConnections, HubStoreErrorFactory

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


class IdentityTransactions:
    """Opens identity units of work over the hub store."""

    def __init__(
        self, store: HubStoreConnections, errors: RepoErrorFactory, store_errors: HubStoreErrorFactory
    ) -> None:
        self._store = store
        self._errors = errors
        self._store_errors = store_errors

    @contextmanager
    def begin(self) -> Generator[IdentityWrites, None, None]:
        with self._store.write("identity_transaction") as conn:
            _lock_identities(conn)
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
