"""``create_engine_from_url``'s sqlite pragmas (``bzh:sql-portable``) — blizzard#512.

Six pragmas applied to every sqlite connection the factory opens: WAL journaling,
NORMAL synchronous, a 5s busy timeout, a 64 MiB page cache, a 256 MiB mmap window, and
in-memory temp storage. A non-sqlite URL gets none of this — the listener is attached
per engine instance, never globally."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text

from blizzard.foundation.store.engine import _set_sqlite_pragmas, create_engine_from_url

pytestmark = pytest.mark.unit


def test_sqlite_engine_applies_every_pragma(tmp_path: Path) -> None:
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'store.db'}")
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA synchronous")).scalar() == 1  # NORMAL
        assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 5000
        assert conn.execute(text("PRAGMA cache_size")).scalar() == -65536
        assert conn.execute(text("PRAGMA mmap_size")).scalar() == 268435456
        assert conn.execute(text("PRAGMA temp_store")).scalar() == 2  # MEMORY
    engine.dispose()


def test_non_sqlite_url_gets_no_pragma_listener(monkeypatch: pytest.MonkeyPatch) -> None:
    # The postgres driver is an opt-in extra (pyproject.toml), not installed by default —
    # stubbing `create_engine` isolates the URL-prefix branch from needing it installed.
    fake_engine = create_engine("sqlite://")
    monkeypatch.setattr("blizzard.foundation.store.engine.create_engine", lambda *a, **k: fake_engine)
    engine = create_engine_from_url("postgresql://user:pass@localhost/blizzard")
    assert engine is fake_engine
    assert not event.contains(engine, "connect", _set_sqlite_pragmas)
    engine.dispose()


def test_a_pre_existing_delete_mode_file_converts_to_wal_on_first_connection(tmp_path: Path) -> None:
    db_path = tmp_path / "store.db"
    # A plain sqlite3 connection, never through the factory, defaults to `delete` mode.
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()

    engine = create_engine_from_url(f"sqlite:///{db_path}")
    with engine.connect() as conn2:
        assert conn2.execute(text("PRAGMA journal_mode")).scalar() == "wal"
    engine.dispose()


def test_dispose_checkpoints_and_removes_the_wal_sidecar(tmp_path: Path) -> None:
    db_path = tmp_path / "store.db"
    wal_path = db_path.with_name(db_path.name + "-wal")
    engine = create_engine_from_url(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, value TEXT)"))
        conn.execute(text("INSERT INTO t (id, value) VALUES (1, 'hello')"))
    assert wal_path.exists()  # WAL still holds the committed row until a checkpoint

    engine.dispose()

    assert not wal_path.exists()
    direct = sqlite3.connect(db_path)
    try:
        assert direct.execute("SELECT value FROM t WHERE id = 1").fetchone() == ("hello",)
    finally:
        direct.close()
