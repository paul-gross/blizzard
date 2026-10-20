"""What the rehearsal reads from a store: the baseline's contract, seeded rows over its columns, and a reflection.

Reads only — rows are seeded by the mock-data CLI. The one write is creating and dropping a leg's own Postgres
database on the server the caller names."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from blizzard.tools.compat.git import Repository
from blizzard.tools.store_contract import document_from_engine
from tests.rehearsal.comparison import Rows


def baseline_contract(repo: Path, tag: str, store: str) -> dict[str, Any]:
    """``contracts/store/<store>.json`` as the baseline tag holds it."""
    text = Repository(repo).show(tag, f"contracts/store/{store}.json")
    assert text is not None, f"{tag} has no contracts/store/{store}.json, so it has no store baseline"
    return json.loads(text)


def baseline_openapi(repo: Path, tag: str) -> dict[str, Any]:
    text = Repository(repo).show(tag, "openapi/hub.openapi.json")
    assert text is not None, f"{tag} has no openapi/hub.openapi.json"
    return json.loads(text)


def read_rows(url: str, contract: dict[str, Any]) -> Rows:
    """Every row of every table ``contract`` declares, keyed by primary key, over the contract's columns present."""
    engine = sa.create_engine(url)
    try:
        present = set(sa.inspect(engine).get_table_names())
        rows: Rows = {}
        with engine.connect() as connection:
            for table, declared in contract["tables"].items():
                if table not in present:
                    continue
                live = {column["name"] for column in sa.inspect(engine).get_columns(table)}
                columns = sorted(name for name in declared["columns"] if name in live)
                key = declared["primary_key"] or columns
                selected = sa.select(*(sa.column(name) for name in columns)).select_from(sa.table(table))
                rows[table] = {
                    repr(tuple(row[k] for k in key)): {name: repr(row[name]) for name in columns}
                    for row in connection.execute(selected).mappings()
                }
        return rows
    finally:
        engine.dispose()


def seeded_only(rows: Rows) -> Rows:
    """The tables a seed actually wrote a row to."""
    return {table: values for table, values in rows.items() if values}


def reflect(url: str, store: str) -> dict[str, Any]:
    """The store document a live store's reflection yields, at its applied revision."""
    engine = sa.create_engine(url)
    try:
        with engine.connect() as connection:
            applied = connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
        return document_from_engine(store, engine, applied)
    finally:
        engine.dispose()


@contextmanager
def postgres_database(server_url: str) -> Iterator[str]:
    """A fresh database on ``server_url``'s server for the span, dropped afterwards; yields its URL."""
    server = sa.make_url(server_url)
    if server.drivername == "postgresql":
        server = server.set(drivername="postgresql+psycopg")
    name = f"rehearsal_{uuid.uuid4().hex[:12]}"
    admin = sa.create_engine(server, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(sa.text(f'CREATE DATABASE "{name}"'))
    try:
        yield server.set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as connection:
            connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
