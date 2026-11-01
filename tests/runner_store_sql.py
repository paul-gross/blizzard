"""Plain-SQL writes and reads against a stopped runner store, held to the names ``contracts/store/runner.json`` pins.

A retained test that seeds or reads a runner store names its tables and columns here instead of discovering them
through SQLAlchemy: every identifier is checked against the contract before any statement runs, and a store not at
the contract's ``head_revision`` is refused, because the contract would not describe the file. Nothing here may run
against a store a daemon holds open."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tests.repo_files import repo_root

CONTRACT_PATH = "contracts/store/runner.json"


def utc_text(moment: datetime | None = None) -> str:
    """``moment`` (now by default) as the naive-UTC text the store's ``DateTime`` columns hold and read back."""
    return (
        (moment or datetime.now(UTC)).astimezone(UTC).replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds")
    )


def load_contract() -> dict[str, Any]:
    """The pinned runner-store contract."""
    loaded: dict[str, Any] = json.loads((repo_root() / CONTRACT_PATH).read_text())
    return loaded


def _pinned(contract: Mapping[str, Any], table: str, columns: Sequence[str]) -> None:
    pinned = contract["tables"].get(table)
    if pinned is None:
        raise ValueError(f"table {table!r} is not pinned by {CONTRACT_PATH}")
    unknown = [name for name in columns if name not in pinned["columns"]]
    if unknown:
        raise ValueError(f"{table!r} columns {unknown} are not pinned by {CONTRACT_PATH}")


def _quoted(names: Sequence[str]) -> str:
    return ", ".join(f'"{name}"' for name in names)


def _stored_revision(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    return None if row is None else str(row[0])


@contextmanager
def _open(store: Path) -> Generator[tuple[sqlite3.Connection, dict[str, Any]]]:
    contract = load_contract()
    conn = sqlite3.connect(store)
    try:
        revision = _stored_revision(conn)
        if revision != contract["head_revision"]:
            raise ValueError(f"{store} is at {revision!r}, not the contract head {contract['head_revision']!r}")
        yield conn, contract
        conn.commit()
    finally:
        conn.close()


def insert(store: Path, table: str, rows: Sequence[Mapping[str, object]]) -> None:
    """Write ``rows`` into ``table`` of the stopped store at ``store``; each row names its columns explicitly."""
    with _open(store) as (conn, contract):
        for values in rows:
            names = list(values)
            _pinned(contract, table, names)
            marks = ", ".join("?" for _ in names)
            conn.execute(f'INSERT INTO "{table}" ({_quoted(names)}) VALUES ({marks})', [*values.values()])


def select(
    store: Path,
    table: str,
    columns: Sequence[str],
    where: Mapping[str, object] | None = None,
    order_by: str | None = None,
) -> list[dict[str, Any]]:
    """The ``columns`` of ``table``'s rows at the stopped store, filtered by equality on ``where`` and ordered."""
    where = where or {}
    with _open(store) as (conn, contract):
        _pinned(contract, table, [*columns, *where, *([order_by] if order_by else [])])
        sql = f'SELECT {_quoted(columns)} FROM "{table}"'
        if where:
            sql += " WHERE " + " AND ".join(f'"{name}" = ?' for name in where)
        if order_by:
            sql += f' ORDER BY "{order_by}"'
        cursor = conn.execute(sql, list(where.values()))
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
