"""``blizzard runner store reset`` — the store moves aside under a timestamped name with every file
that held it, a fresh one is migrated in its place, nothing is ever deleted, and the verb refuses a
store a daemon still holds or one that is not a SQLite file."""

from __future__ import annotations

import re
import socket
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.cli.main import blizzard
from blizzard.runner.config import RunnerConfig
from blizzard.runner.runtime import (
    StoreResetRefused,
    migration_runner,
    set_aside_sqlite,
    sqlite_store_file,
)
from tests.runner_init_fakes import init_runner

pytestmark = pytest.mark.component

_NOW = datetime(2026, 10, 8, 12, 30, 5, tzinfo=UTC)
_STAMP = "20261008T123005Z"


def _initialized(tmp_path: Path) -> Path:
    root = tmp_path / "runner"
    result = init_runner().invoke(blizzard, ["runner", "init", str(root)])
    assert result.exit_code == 0, result.output
    return root


def _identity_ids(database: Path) -> list[str]:
    with closing(sqlite3.connect(database)) as conn:
        return [row[0] for row in conn.execute("SELECT runner_id FROM runner_identity")]


def _seed_identity(database: Path, runner_id: str) -> None:
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute(
            "INSERT INTO runner_identity (runner_id, runner_name, registered_at, projects_answered_at) "
            "VALUES (?, 'r', ?, ?)",
            (runner_id, _NOW.isoformat(), _NOW.isoformat()),
        )
        conn.execute(
            "INSERT INTO hub_served_projects (position, entry, answered_at) VALUES (0, 'old-slug', ?)",
            (_NOW.isoformat(),),
        )


def _served_entries(database: Path) -> list[str]:
    with closing(sqlite3.connect(database)) as conn:
        return [row[0] for row in conn.execute("SELECT entry FROM hub_served_projects")]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("url", "expected"),
    [("sqlite:////abs/data/runner.db", Path("/abs/data/runner.db")), ("sqlite+pysqlite:///rel.db?x=1", Path("rel.db"))],
)
def test_a_sqlite_url_names_its_database_file(url: str, expected: Path) -> None:
    assert sqlite_store_file(url) == expected


@pytest.mark.unit
@pytest.mark.parametrize("url", ["postgresql://u@db/runner", "sqlite://", "sqlite:///:memory:"])
def test_a_store_with_no_file_cannot_be_set_aside_and_says_why(url: str) -> None:
    with pytest.raises(StoreResetRefused, match="not a SQLite file"):
        sqlite_store_file(url)


def test_reset_moves_the_store_aside_keeping_its_rows_and_migrates_a_fresh_one(tmp_path: Path) -> None:
    root = _initialized(tmp_path)
    config = RunnerConfig.load(root)
    database = sqlite_store_file(config.db_url)
    _seed_identity(database, "rn_old")

    result = init_runner().invoke(blizzard, ["runner", "store", "reset", str(root)])

    assert result.exit_code == 0, result.output
    aside = [p for p in database.parent.iterdir() if p.name.startswith("runner.db.")]
    assert [bool(re.fullmatch(r"runner\.db\.\d{8}T\d{6}Z", p.name)) for p in aside] == [True]
    assert _identity_ids(aside[0]) == ["rn_old"]
    assert _identity_ids(database) == []
    assert _served_entries(aside[0]) == ["old-slug"]
    assert _served_entries(database) == []
    assert migration_runner(config).current_revision() == migration_runner(config).script_head()


def test_set_aside_moves_every_sibling_under_the_stamped_name_and_deletes_nothing(tmp_path: Path) -> None:
    database = tmp_path / "runner.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.execute("INSERT INTO t VALUES ('kept')")
    (tmp_path / "runner.db-journal").write_bytes(b"")

    moved = set_aside_sqlite(database, now=_NOW)

    assert {p.name for p in moved.moved} == {f"runner.db.{_STAMP}", f"runner.db.{_STAMP}-journal"}
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"runner.db.{_STAMP}", f"runner.db.{_STAMP}-journal"]
    with sqlite3.connect(tmp_path / f"runner.db.{_STAMP}") as conn:
        assert conn.execute("SELECT v FROM t").fetchall() == [("kept",)]


def test_set_aside_folds_the_write_ahead_log_into_the_moved_database(tmp_path: Path) -> None:
    database = tmp_path / "runner.db"
    holder = sqlite3.connect(database)
    holder.execute("PRAGMA journal_mode=WAL")
    holder.execute("PRAGMA wal_autocheckpoint=0")
    holder.execute("CREATE TABLE t (v TEXT)")
    holder.execute("INSERT INTO t VALUES ('in the log')")
    holder.commit()
    assert (tmp_path / "runner.db-wal").stat().st_size > 0

    set_aside_sqlite(database, now=_NOW)
    holder.close()

    with sqlite3.connect(tmp_path / f"runner.db.{_STAMP}") as conn:
        assert conn.execute("SELECT v FROM t").fetchall() == [("in the log",)]
    assert not database.exists()


def test_set_aside_refuses_a_stamped_name_already_taken_moving_nothing(tmp_path: Path) -> None:
    database = tmp_path / "runner.db"
    sqlite3.connect(database).close()
    (tmp_path / f"runner.db.{_STAMP}").write_bytes(b"earlier")

    with pytest.raises(StoreResetRefused, match="already exists"):
        set_aside_sqlite(database, now=_NOW)

    assert database.exists()
    assert (tmp_path / f"runner.db.{_STAMP}").read_bytes() == b"earlier"


def test_reset_refuses_while_a_daemon_answers_on_the_socket_leaving_the_store(tmp_path: Path) -> None:
    root = _initialized(tmp_path)
    database = sqlite_store_file(RunnerConfig.load(root).db_url)
    _seed_identity(database, "rn_live")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(RunnerConfig.socket_path_for(root)))
    server.listen(1)

    def _answer() -> None:
        conn, _ = server.accept()
        with conn:
            conn.recv(4096)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")

    threading.Thread(target=_answer, daemon=True).start()
    try:
        result = init_runner().invoke(blizzard, ["runner", "store", "reset", str(root)])
    finally:
        server.close()

    assert result.exit_code != 0
    assert "a runner daemon is serving at" in result.output
    assert _identity_ids(database) == ["rn_live"]
    assert not [p for p in database.parent.iterdir() if p.name.startswith("runner.db.")]


def test_reset_refuses_a_store_that_is_not_a_sqlite_file_naming_it(tmp_path: Path) -> None:
    root = _initialized(tmp_path)
    config_path = RunnerConfig.load(root).config_path
    text = config_path.read_text()
    db_line = next(line for line in text.splitlines() if line.startswith("db_url"))
    config_path.write_text(text.replace(db_line, 'db_url = "postgresql://runner@db.test/runner"'))

    result = init_runner().invoke(blizzard, ["runner", "store", "reset", str(root)])

    assert result.exit_code != 0
    assert "postgresql://runner@db.test/runner is not a SQLite file" in result.output
