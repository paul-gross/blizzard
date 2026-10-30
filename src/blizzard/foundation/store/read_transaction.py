"""One read transaction over one connection: every statement on it sees the store as of one moment.

pysqlite issues no ``BEGIN`` before a ``SELECT``, so without one each statement takes its own snapshot and sees a
concurrent commit; Postgres's default ``READ COMMITTED`` does the same. The opener holds a ``BEGIN`` on SQLite, and
opens ``REPEATABLE READ`` read-only on Postgres, before the first statement. It always ends in rollback."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import Connection, Engine


@contextmanager
def read_transaction(engine: Engine) -> Generator[Connection, None, None]:
    """A connection of ``engine`` inside one read transaction, rolled back and closed on leaving the ``with``."""
    with engine.connect() as conn:
        if engine.dialect.name == "sqlite":
            conn.exec_driver_sql("BEGIN")
        else:
            conn.execution_options(isolation_level="REPEATABLE READ", postgresql_readonly=True)
        try:
            yield conn
        finally:
            conn.rollback()
