"""The portable SQLAlchemy engine factory (``bzh:sql-portable``).

The engine is sqlite or postgres, selected only by the configured URL; the schema stays inside
SQLAlchemy's portable surface. A sqlite engine runs in WAL mode, so a live store also has a
``-wal`` sidecar.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

from sqlalchemy import Engine, create_engine, event

from blizzard.foundation.store.utc import sqlite_adapt_date, sqlite_adapt_datetime

# A raw datetime reaching the driver (a `text()` bind, which SQLAlchemy's `DateTime` type never sees) is adapted by
# sqlite3's default adapter, deprecated since Python 3.12. Registering the same conversion explicitly keeps the
# on-disk format and silences the warning.
sqlite3.register_adapter(datetime, sqlite_adapt_datetime)
sqlite3.register_adapter(date, sqlite_adapt_date)


def create_engine_from_url(url: str) -> Engine:
    """Build an engine from a store URL, applying the sqlite-safe connect args."""
    connect_args: dict[str, object] = {}
    if url.startswith("sqlite"):
        # The store is served from multiple threads; sqlite's default same-thread check rejects that.
        connect_args["check_same_thread"] = False
    engine = create_engine(url, future=True, connect_args=connect_args)
    if url.startswith("sqlite"):
        event.listen(engine, "connect", set_sqlite_pragmas)
    return engine


def set_sqlite_pragmas(dbapi_connection: sqlite3.Connection, connection_record: object) -> None:
    """Every connect-time pragma this store's sqlite connections get.

    Runs on the raw DBAPI connection before any transaction opens, so `journal_mode`
    always takes effect. `foreign_keys` stays off; turning it on is a separate decision."""
    dbapi_connection.execute("PRAGMA journal_mode=WAL")
    dbapi_connection.execute("PRAGMA synchronous=NORMAL")
    dbapi_connection.execute("PRAGMA busy_timeout=5000")
    dbapi_connection.execute("PRAGMA cache_size=-65536")
    dbapi_connection.execute("PRAGMA mmap_size=268435456")
    dbapi_connection.execute("PRAGMA temp_store=MEMORY")
