"""SQLAlchemy adapter for the identity unit of work (package-private).

:meth:`IdentityTransactions.begin` opens one write transaction and builds every identity repository over connections
pinned to it: their ``read`` and ``write`` hand back that one connection rather than opening their own, so nothing
commits until the unit of work does. A tenant-owned write joins the same transaction through
:meth:`_TransactionConnections.scoped`, whose connections are that tenant's builders over the one connection, opened
under the tenant's lock as any scoped write is."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

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
            yield self._writes(_TransactionConnections(conn, self._store_errors))

    def _writes(self, connections: HubStoreConnections) -> IdentityWrites:
        return IdentityWrites(
            users=UserRepository(connections, self._errors),
            identities=IdentityRepository(connections, self._errors),
            sessions=SessionRepository(connections, self._errors),
            memberships=MembershipRepository(connections),
            hub_admins=HubAdminRepository(connections),
            bootstrap=SuperuserBootstrapRepository(connections),
            auth_facts=AuthFactsRepository(connections),
        )


def _conforms_identity_transactions(x: IdentityTransactions) -> IIdentityTransactions:
    return x
