"""A host directory has one owner and one graduated shape (component tier): ``host`` and ``migrate`` refuse a
directory that has not graduated and change nothing; adoption, ``migrate --down``, ``store reset`` and ``init``'s
set-aside hold the directory and refuse while its lock is held or a pre-host daemon answers on ``runner.sock``; a
plain upgrade refuses only the daemon; a store a configured ``db_url`` names is upgraded in place."""

from __future__ import annotations

import hashlib
import os
import shutil
import socket
import sqlite3
import threading
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from blizzard.cli.main import blizzard
from blizzard.runner.config_table import ConfigError
from blizzard.runner.host_layout import HostLayout, HostLock
from blizzard.runner.runtime import migration_runner
from tests.config_inputs import host_runtime_of
from tests.runner_init_fakes import init_runner

pytestmark = pytest.mark.component

_NOW = datetime(2026, 10, 13, 9, 0, 0, tzinfo=UTC)
_STAMP = "20261013T090000Z"


def _tree(root: Path) -> dict[str, str]:
    """Every file under ``root`` by relative path with its content hash — what "changed nothing" compares. The lock
    file is the directory's owner marker, not its content, and holds a pid."""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "host.lock"
    }


def _host(tmp_path: Path) -> Path:
    root = tmp_path / "host"
    host_runtime_of(root).init()
    return root


def _pre_host(tmp_path: Path) -> Path:
    """A directory a pre-host runner left: its store at ``data/runner.db`` (a write-ahead sibling beside it) and its
    ``blizzard-runner.toml``, with the host file's store renamed away."""
    root = _host(tmp_path)
    layout = HostLayout(root)
    with closing(sqlite3.connect(layout.store_path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE marker (v TEXT)")
        conn.execute("INSERT INTO marker VALUES ('legacy')")
        conn.commit()
    with closing(sqlite3.connect(layout.store_path)) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    layout.store_path.rename(layout.legacy_store_path)
    return root


def _answering(root: Path) -> socket.socket:
    """A listener on the pre-host ``runner.sock`` that answers the health probe."""
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(HostLayout(root).legacy_socket_path))
    server.listen(4)

    def _serve() -> None:
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            with conn:
                conn.recv(4096)
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")

    threading.Thread(target=_serve, daemon=True).start()
    return server


def _answered_by(root: Path) -> Callable[[Path], str | None]:
    return lambda sock: f"a runner daemon is serving at {sock}" if sock == HostLayout(root).legacy_socket_path else None


# --- graduation ---------------------------------------------------------------------------------


def test_host_and_migrate_refuse_a_directory_that_has_not_graduated_and_change_nothing(tmp_path: Path) -> None:
    root = tmp_path / "pre-host"
    (root / "data").mkdir(parents=True)
    (root / "blizzard-runner.toml").write_text('db_url = "sqlite:///x"\n')
    (root / "data" / "runner.db").write_bytes(b"store")
    (root / ".env").write_text("BZ_HUB_TOKEN=t\n")
    before = _tree(root)

    for verb in (
        ["host", "--dir", str(root)],
        ["host", "--dir", str(root), "--check"],
        ["migrate", "--dir", str(root)],
    ):
        result = CliRunner().invoke(blizzard, ["runner", *verb])
        assert result.exit_code != 0, verb
        assert "has not graduated" in result.output and "docs/upgrade.md" in result.output, result.output

    assert _tree(root) == before
    assert sorted(path.name for path in root.iterdir()) == [".env", "blizzard-runner.toml", "data"]


def test_init_refuses_an_ungraduated_directory_naming_graduation(tmp_path: Path) -> None:
    root = tmp_path / "pre-host"
    root.mkdir()
    (root / "blizzard-runner.toml").write_text('db_url = "sqlite:///x"\n')
    before = _tree(root)

    result = init_runner().invoke(blizzard, ["runner", "init", str(root), "--allow-readd"])

    assert result.exit_code != 0 and "docs/upgrade.md" in result.output
    assert _tree(root) == before


def test_a_store_left_with_no_config_beside_it_is_refused_naming_graduation_unless_set_aside(tmp_path: Path) -> None:
    root = tmp_path / "left"
    (root / "data").mkdir(parents=True)
    with closing(sqlite3.connect(root / "data" / "runner.db")) as conn:
        conn.execute("CREATE TABLE t (v TEXT)")
    before = _tree(root)

    refused = init_runner().invoke(blizzard, ["runner", "init", str(root)])
    assert refused.exit_code != 0
    assert "graduation procedure in docs/upgrade.md" in refused.output and "--allow-readd" in refused.output
    assert _tree(root) == before

    allowed = init_runner().invoke(blizzard, ["runner", "init", str(root), "--allow-readd"])
    assert allowed.exit_code == 0, allowed.output
    assert (root / "blizzard-host.toml").is_file()
    assert [p.name for p in (root / "data").iterdir() if p.name.startswith("runner.db.")] != []
    assert (root / "data" / "host.db").is_file() and not (root / "data" / "runner.db").exists()


# --- adoption and the upgrade -------------------------------------------------------------------


def test_migrate_adopts_the_pre_host_store_as_the_host_store_and_upgrades_it(tmp_path: Path) -> None:
    root = _pre_host(tmp_path)
    layout = HostLayout(root)

    result = CliRunner().invoke(blizzard, ["runner", "migrate", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    assert layout.store_path.is_file() and not layout.legacy_store_path.exists()
    assert not list(layout.data_dir.glob("runner.db*"))
    with closing(sqlite3.connect(layout.store_path)) as conn:
        assert conn.execute("SELECT v FROM marker").fetchall() == [("legacy",)]
    config = host_runtime_of(root).init()
    assert migration_runner(config).is_current()


def test_migrate_upgrades_a_store_a_configured_db_url_names_in_place_leaving_the_pre_host_store_alone(
    tmp_path: Path,
) -> None:
    root = _pre_host(tmp_path)
    layout = HostLayout(root)
    named = tmp_path / "elsewhere.db"
    shutil.copyfile(layout.legacy_store_path, named)
    layout.config_path.write_text(f'db_url = "sqlite:///{named}"\n' + layout.config_path.read_text())
    legacy_bytes = layout.legacy_store_path.read_bytes()

    result = CliRunner().invoke(blizzard, ["runner", "migrate", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    assert layout.legacy_store_path.read_bytes() == legacy_bytes
    assert not layout.store_path.exists()
    with closing(sqlite3.connect(named)) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() is not None


def test_migrate_and_check_refuse_a_configured_store_a_pre_host_daemon_still_serves(tmp_path: Path) -> None:
    """A copy whose host file keeps a ``db_url`` naming the live ``<dir>/data/runner.db`` would migrate the live store
    under its running daemon: the daemon answers on that directory's ``runner.sock``, which both verbs probe."""
    root = _pre_host(tmp_path)
    layout = HostLayout(root)
    live = tmp_path / "live"
    (live / "data").mkdir(parents=True)
    named = live / "data" / "runner.db"
    shutil.copyfile(layout.legacy_store_path, named)
    layout.config_path.write_text(f'db_url = "sqlite:///{named}"\n' + layout.config_path.read_text())
    before = named.read_bytes()

    server = _answering(live)
    try:
        migrated = CliRunner().invoke(blizzard, ["runner", "migrate", "--dir", str(root)])
        checked = CliRunner().invoke(blizzard, ["runner", "host", "--check", "--dir", str(root)])
    finally:
        server.close()

    for result in (migrated, checked):
        assert result.exit_code != 0
        assert str(live / "runner.sock") in result.output and "drop the `db_url`" in result.output
    assert named.read_bytes() == before


def test_a_plain_upgrade_of_an_existing_host_store_succeeds_while_a_host_holds_the_lock(tmp_path: Path) -> None:
    root = _host(tmp_path)
    migration_runner(host_runtime_of(root).init()).downgrade("20261011_1000_runner_scope")

    with HostLock.acquire(HostLayout(root)):
        result = CliRunner().invoke(blizzard, ["runner", "migrate", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    assert migration_runner(host_runtime_of(root).init()).is_current()


# --- one owner ----------------------------------------------------------------------------------


def _refusals_while_held(root: Path, holder: Callable[[Path], str | None] | None = None) -> dict[str, str]:
    """What each verb that moves or rewrites the store says while the directory is held, by verb."""
    runtime = host_runtime_of(root, holder=holder) if holder is not None else host_runtime_of(root)
    said: dict[str, str] = {}
    for verb, call in (
        ("migrate --down", lambda: runtime.migrate(down="base")),
        ("store reset", lambda: runtime.reset_store(now=_NOW)),
    ):
        with pytest.raises(ConfigError) as refused:
            call()
        said[verb] = str(refused.value)
    return said


def test_every_verb_that_moves_the_store_but_a_plain_upgrade_refuses_while_the_lock_is_held_naming_its_pid(
    tmp_path: Path,
) -> None:
    adopted = _host(tmp_path / "adopted")
    pre_host = _pre_host(tmp_path / "pre")
    before = (_tree(adopted), _tree(pre_host))

    with HostLock.acquire(HostLayout(adopted)):
        said = _refusals_while_held(adopted)
    with HostLock.acquire(HostLayout(pre_host)), pytest.raises(ConfigError) as adoption:
        host_runtime_of(pre_host).migrate()
    said["adoption"] = str(adoption.value)

    assert set(said) == {"migrate --down", "store reset", "adoption"}
    assert all(f"pid {os.getpid()}" in message for message in said.values()), said
    assert (_tree(adopted), _tree(pre_host)) == before


def test_the_set_aside_of_a_pre_host_store_refuses_while_the_lock_is_held(tmp_path: Path) -> None:
    root = tmp_path / "left"
    (root / "data").mkdir(parents=True)
    with closing(sqlite3.connect(root / "data" / "runner.db")) as conn:
        conn.execute("CREATE TABLE t (v TEXT)")

    with HostLock.acquire(HostLayout(root)), pytest.raises(ConfigError, match=f"pid {os.getpid()}"):
        host_runtime_of(root).set_aside_pre_host_store(now=_NOW)

    assert (root / "data" / "runner.db").is_file()


def test_every_verb_that_moves_the_store_refuses_while_a_pre_host_daemon_answers_naming_its_socket(
    tmp_path: Path,
) -> None:
    adopted = _host(tmp_path / "adopted")
    root = _pre_host(tmp_path / "pre")
    before = (_tree(adopted), _tree(root))

    said = _refusals_while_held(adopted, _answered_by(adopted))
    with pytest.raises(ConfigError) as refused:
        host_runtime_of(root, holder=_answered_by(root)).migrate()
    said["adoption"] = str(refused.value)

    sockets = {"adoption": HostLayout(root).legacy_socket_path}
    assert all(
        str(sockets.get(verb, HostLayout(adopted).legacy_socket_path)) in message for verb, message in said.items()
    ), said
    assert (_tree(adopted), _tree(root)) == before


def test_a_plain_upgrade_of_an_existing_store_refuses_a_pre_host_daemon_and_nothing_else(tmp_path: Path) -> None:
    root = _host(tmp_path)
    layout = HostLayout(root)
    migration_runner(host_runtime_of(root).init()).downgrade("20261011_1000_runner_scope")
    holder = _answered_by(root)

    with pytest.raises(ConfigError, match=r"runner\.sock"):
        host_runtime_of(root, holder=holder).migrate()

    server = _answering(root)
    try:
        result = CliRunner().invoke(blizzard, ["runner", "migrate", "--dir", str(root)])
    finally:
        server.close()
    assert result.exit_code != 0 and str(layout.legacy_socket_path) in result.output
    assert "runner daemon is serving" in result.output


def test_host_refuses_to_start_while_the_directory_is_held_or_a_pre_host_daemon_answers(tmp_path: Path) -> None:
    root = _host(tmp_path)

    with HostLock.acquire(HostLayout(root)):
        held = CliRunner().invoke(blizzard, ["runner", "host", "--dir", str(root)])
    assert held.exit_code != 0 and f"pid {os.getpid()}" in held.output

    server = _answering(root)
    try:
        answered = CliRunner().invoke(blizzard, ["runner", "host", "--dir", str(root)])
    finally:
        server.close()
    assert answered.exit_code != 0 and str(HostLayout(root).legacy_socket_path) in answered.output
    assert "serving blizzard-runner" not in held.output + answered.output


def test_the_lock_is_released_with_its_holder_so_a_crash_leaves_none_behind(tmp_path: Path) -> None:
    root = _host(tmp_path)
    layout = HostLayout(root)

    with HostLock.acquire(layout):
        pass
    lock = HostLock.acquire(layout)
    lock.release()

    assert layout.lock_path.is_file()
