"""SQLAlchemy adapter for the store-status seam (package-private).

The one place a readiness read touches the engine (``bzh:pluggable-seams``). It
opens a connection, reads the store's current Alembic revision, and reports a
:class:`StoreStatus`. A connection failure is caught and reported as
``reachable=False`` — readiness asks "can I reach my store", so an unreachable
store is a value the caller acts on, not an exception it must handle.
"""

from __future__ import annotations

from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from blizzard.foundation.store.status import IStoreStatusReader, StoreStatus


class SqlAlchemyStoreStatusReader:
    """Reads reachability + applied revision from a store engine."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def read_status(self) -> StoreStatus:
        try:
            with self._engine.connect() as conn:
                revision = MigrationContext.configure(conn).get_current_revision()
        except SQLAlchemyError as exc:
            return StoreStatus(reachable=False, revision=None, detail=str(exc))
        return StoreStatus(reachable=True, revision=revision)


# Typecheck-time conformance sentinel (matches the exemplar): pyright rejects the
# return if the adapter drifts from the read-only seam it implements.
def _conforms_store_status_reader(x: SqlAlchemyStoreStatusReader) -> IStoreStatusReader:
    return x
