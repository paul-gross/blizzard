"""The portable SQLAlchemy engine factory (``bzh:sql-portable``).

Both stores run on sqlite (the fast local default and what tests use) or postgres,
selected only by the configured URL — the schema stays inside SQLAlchemy's
portable surface, so postgres is a URL change, not a second test matrix. A sqlite
engine runs in WAL mode, so the store's ``.db`` file alone is a complete copy of the
data only right after a clean shutdown — a live store also has a ``-wal`` sidecar.
"""

from __future__ import annotations

import sqlite3

from sqlalchemy import Engine, create_engine, event


def create_engine_from_url(url: str) -> Engine:
    """Build an engine from a store URL, applying the sqlite-safe connect args."""
    connect_args: dict[str, object] = {}
    if url.startswith("sqlite"):
        # The store is served from multiple threads; sqlite's default same-thread check rejects that.
        connect_args["check_same_thread"] = False
    engine = create_engine(url, future=True, connect_args=connect_args)
    if url.startswith("sqlite"):
        event.listen(engine, "connect", _set_sqlite_pragmas)
    return engine


def _set_sqlite_pragmas(dbapi_connection: sqlite3.Connection, connection_record: object) -> None:
    """Every connect-time pragma this store's sqlite connections get.

    Runs on the raw DBAPI connection before any transaction opens, so `journal_mode`
    always takes effect. `foreign_keys` stays off; turning it on is a separate decision."""
    dbapi_connection.execute("PRAGMA journal_mode=WAL")
    dbapi_connection.execute("PRAGMA synchronous=NORMAL")
    dbapi_connection.execute("PRAGMA busy_timeout=5000")
    dbapi_connection.execute("PRAGMA cache_size=-65536")
    dbapi_connection.execute("PRAGMA mmap_size=268435456")
    dbapi_connection.execute("PRAGMA temp_store=MEMORY")
