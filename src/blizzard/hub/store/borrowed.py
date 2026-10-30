"""Hub store connections borrowed over one already-open read transaction (``bzh:tenant-scoped-store``).

:meth:`HubStoreConnections.read_transaction` opens the transaction and hands out :class:`BorrowedConnections`; every
``read`` of them, and of each :class:`BorrowedScopedConnections` they give, yields that one connection without closing
it. Rescoping binds the new tenant on the same connection, so moving between tenants is only a change of the scope the
statement builders apply, and the statement guard still refuses an unscoped tenant read. Every write refuses."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import Connection
from sqlalchemy.exc import SQLAlchemyError

from blizzard.hub.store.errors import BorrowedWrite, HubStoreConnections, HubStoreErrorFactory
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.tenancy import StoreScope


class BorrowedConnections(HubStoreConnections):
    """:class:`HubStoreConnections` over ``conn``, a connection its read transaction owns and closes."""

    def __init__(self, conn: Connection, errors: HubStoreErrorFactory) -> None:
        HubStoreConnections.__init__(self, conn.engine, errors)
        self._conn = conn

    def scoped(self, scope: StoreScope) -> ScopedConnections:
        return BorrowedScopedConnections(self._conn, self._errors, scope)

    @contextmanager
    def read(self, operation: str) -> Generator[Connection, None, None]:
        try:
            yield self._conn
        except SQLAlchemyError as exc:
            raise self._errors.from_driver(exc, operation=operation) from exc

    @contextmanager
    def write(
        self, operation: str, *, expect: tuple[type[BaseException], ...] = ()
    ) -> Generator[Connection, None, None]:
        raise BorrowedWrite(operation)
        yield self._conn  # pragma: no cover - unreachable; makes this a generator


class BorrowedScopedConnections(ScopedConnections):
    """:class:`ScopedConnections` over ``conn``, a connection its read transaction owns and closes."""

    def __init__(self, conn: Connection, errors: HubStoreErrorFactory, scope: StoreScope) -> None:
        ScopedConnections.__init__(self, conn.engine, errors, scope)
        self._conn = conn

    @contextmanager
    def read(self, operation: str) -> Generator[Connection, None, None]:
        try:
            yield self._bind(self._conn)
        except SQLAlchemyError as exc:
            raise self._errors.from_driver(exc, operation=operation) from exc

    @contextmanager
    def write(
        self, operation: str, *, expect: tuple[type[BaseException], ...] = ()
    ) -> Generator[Connection, None, None]:
        raise BorrowedWrite(operation)
        yield self._conn  # pragma: no cover - unreachable; makes this a generator

    @contextmanager
    def founding(
        self, operation: str, *, name: str, created_at: datetime, created_by: str
    ) -> Generator[Connection, None, None]:
        raise BorrowedWrite(operation)
        yield self._conn  # pragma: no cover - unreachable; makes this a generator

    @contextmanager
    def teardown(self, operation: str) -> Generator[Connection, None, None]:
        raise BorrowedWrite(operation)
        yield self._conn  # pragma: no cover - unreachable; makes this a generator
