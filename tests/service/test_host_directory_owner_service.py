"""A host directory has one graduated shape and one owner, proven on live ``blizzard-runner`` processes (service
tier): ``host`` and ``migrate`` refuse a directory that has not graduated and leave it byte for byte; while a running
host holds ``data/host.lock`` — or another process does — a second ``host``, adoption, ``migrate --down``,
``store reset`` and ``init``'s set-aside each refuse naming the holder's pid, and while a pre-host daemon answers on
``runner.sock`` each refuses naming the socket; a plain upgrade runs beside a running host; and a store a configured
``db_url`` names is upgraded in place with ``data/runner.db`` untouched. Run with ``BLIZZARD_SERVICE=1``."""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import httpx
import pytest

from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.runner.host_config import HostConfig
from blizzard.runner.host_layout import HostLayout
from blizzard.runner.runtime import migration_runner
from tests.fleet_launchers import await_http, terminate
from tests.host_files import init_runner_environment, write_host_file
from tests.runner_join import RUNNER_BIN, run_runner_init, runner_spawn_env
from tests.service.support import mock_hub, poll_until, require_mock_fleet, service_gate
from tests.support import daemon_log_sink, free_port, read_daemon_log

pytestmark = [pytest.mark.service, service_gate]

_PARENT = "20261011_1000_runner_scope"


def _tree(root: Path) -> dict[str, str]:
    """Every file under ``root`` by relative path with its content hash; the lock file names its last holder's pid,
    and the daemon log is the test's own."""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name not in {"host.lock", "daemon.log"}
    }


def _held_shape(root: Path) -> tuple[set[str], str, str]:
    """What a refused verb would change under a running host, whose own store churns as it ticks: the files present —
    a set-aside or a fresh store adds one — and the host file and token bytes."""
    return (
        {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()},
        HostLayout(root).config_path.read_text(),
        (root / ".env").read_text(),
    )


def _runner(*args: str, hub_url: str = "http://127.0.0.1:9") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(RUNNER_BIN), *args],
        capture_output=True,
        text=True,
        env=runner_spawn_env(hub_url),
        timeout=120,
        check=False,
    )


def _said(done: subprocess.CompletedProcess[str]) -> str:
    return done.stdout + done.stderr


def _migrations(root: Path) -> MigrationRunner:
    """The migrations of ``root``'s configured store, read without upgrading it."""
    return migration_runner(HostConfig.load(root))


def _host_dir(root: Path) -> Path:
    """A host directory holding its migrated store, its runner given an id and a token."""
    init_runner_environment(root)
    return root


def _pre_host(root: Path) -> Path:
    """A graduated host file beside the pre-host store it has yet to adopt."""
    _host_dir(root)
    layout = HostLayout(root)
    layout.store_path.rename(layout.legacy_store_path)
    return root


def _left_store(root: Path) -> Path:
    """A ``data/runner.db`` a pre-host runner left with no config beside it — what ``init --allow-readd`` sets aside."""
    (root / "data").mkdir(parents=True)
    with closing(sqlite3.connect(root / "data" / "runner.db")) as conn:
        conn.execute("CREATE TABLE t (v TEXT)")
    return root


@contextlib.contextmanager
def _lock_holder(root: Path) -> Iterator[int]:
    """Another process holding ``root``'s lock, as a host does for its lifetime; yields its pid."""
    log = root.parent / f"{root.name}.holder.log"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time\n"
            "from pathlib import Path\n"
            "from blizzard.runner.host_layout import HostLayout, HostLock\n"
            "lock = HostLock.acquire(HostLayout(Path(sys.argv[1])))\n"
            "print('held', flush=True)\n"
            "time.sleep(600)\n",
            str(root),
        ],
        stdout=daemon_log_sink(log),
        text=True,
    )
    try:
        assert poll_until(lambda: "held" in read_daemon_log(log)), "the lock holder never took the lock"
        yield proc.pid
    finally:
        terminate(proc)


@contextlib.contextmanager
def _pre_host_daemon(root: Path) -> Iterator[Path]:
    """A listener on ``root``'s pre-host ``runner.sock`` answering every request ``200`` — a pre-host daemon still
    serving the directory; yields the socket."""
    path = HostLayout(root).legacy_socket_path
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(8)

    def _serve() -> None:
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            with conn:
                conn.recv(65536)
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}")

    threading.Thread(target=_serve, daemon=True).start()
    try:
        yield path
    finally:
        server.close()
        path.unlink(missing_ok=True)


@contextlib.contextmanager
def _running_host(root: Path, hub_url: str) -> Iterator[subprocess.Popen[str]]:
    """``blizzard-runner host`` over ``root``, yielded once its local API answers."""
    config = init_runner_environment(root)
    port = free_port()
    write_host_file(dataclasses.replace(config, host="127.0.0.1", port=port, hub_url=hub_url))
    log = root / "daemon.log"
    proc = subprocess.Popen(
        [str(RUNNER_BIN), "host", "--dir", str(root)],
        env=runner_spawn_env(hub_url),
        stdout=daemon_log_sink(log),
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10.0)
    try:
        await_http(proc, client, "/api/health", log=log)
        yield proc
    finally:
        client.close()
        terminate(proc)


def test_host_and_migrate_refuse_an_ungraduated_directory_naming_the_procedure_and_change_nothing(
    tmp_path: Path,
) -> None:
    root = tmp_path / "pre-host"
    (root / "data").mkdir(parents=True)
    (root / "blizzard-runner.toml").write_text(f'db_url = "sqlite:///{root}/data/runner.db"\nname = "runner-local"\n')
    with closing(sqlite3.connect(root / "data" / "runner.db")) as conn:
        conn.execute("CREATE TABLE marker (v TEXT)")
    (root / ".env").write_text("BZ_HUB_TOKEN=tok_pre_host\n")
    before = _tree(root)

    for verb in (
        ["host", "--dir", str(root)],
        ["host", "--dir", str(root), "--check"],
        ["migrate", "--dir", str(root)],
    ):
        refused = _runner(*verb)
        assert refused.returncode != 0, verb
        assert "has not graduated" in _said(refused) and "docs/upgrade.md" in _said(refused), _said(refused)

    assert _tree(root) == before
    assert sorted(path.name for path in root.iterdir()) == [".env", "blizzard-runner.toml", "data"]


def test_a_running_host_holds_its_directory_against_every_verb_that_moves_its_store_but_a_plain_upgrade(
    tmp_path: Path,
) -> None:
    bin_dir = require_mock_fleet()
    hub_port = free_port()
    hub_url = f"http://127.0.0.1:{hub_port}"
    root = tmp_path / "host"
    with mock_hub(bin_dir, hub_port), _running_host(root, hub_url) as host:
        before = _held_shape(root)
        held = f"pid {host.pid}"

        second = _runner("host", "--dir", str(root), hub_url=hub_url)
        down = _runner("migrate", "--dir", str(root), "--down", "base", hub_url=hub_url)
        reset = _runner("store", "reset", str(root), hub_url=hub_url)
        for refused in (second, down, reset):
            assert refused.returncode != 0 and held in _said(refused), _said(refused)
        assert "serving blizzard-runner" not in _said(second)

        upgrade = _runner("migrate", "--dir", str(root), hub_url=hub_url)
        assert upgrade.returncode == 0, _said(upgrade)
        assert host.poll() is None
        assert _held_shape(root) == before


def test_adoption_set_aside_and_an_upgrade_from_the_parent_revision_under_another_holder(tmp_path: Path) -> None:
    adopting = _pre_host(tmp_path / "adopting")
    left = _left_store(tmp_path / "left")
    upgrading = _host_dir(tmp_path / "upgrading")
    _migrations(upgrading).downgrade(_PARENT)

    with _lock_holder(adopting) as adopter, _lock_holder(left) as setter, _lock_holder(upgrading):
        adopting_before, left_before = _tree(adopting), _tree(left)

        adoption = _runner("migrate", "--dir", str(adopting))
        set_aside = run_runner_init(left, "http://127.0.0.1:9", allow_readd=True)
        upgrade = _runner("migrate", "--dir", str(upgrading))

        assert adoption.returncode != 0 and f"pid {adopter}" in _said(adoption), _said(adoption)
        assert set_aside.returncode != 0 and f"pid {setter}" in _said(set_aside), _said(set_aside)
        assert upgrade.returncode == 0, _said(upgrade)
    assert (_tree(adopting), _tree(left)) == (adopting_before, left_before)
    assert _migrations(upgrading).is_current()


def test_every_verb_that_moves_the_store_refuses_while_a_pre_host_daemon_answers_naming_its_socket(
    tmp_path: Path,
) -> None:
    host = _host_dir(tmp_path / "host")
    adopting = _pre_host(tmp_path / "adopting")
    left = _left_store(tmp_path / "left")

    with (
        _pre_host_daemon(host) as host_sock,
        _pre_host_daemon(adopting) as adopting_sock,
        _pre_host_daemon(left) as left_sock,
    ):
        trees = {root: _tree(root) for root in (host, adopting, left)}
        said = {
            "host": (_runner("host", "--dir", str(host)), host_sock),
            "migrate --down": (_runner("migrate", "--dir", str(host), "--down", "base"), host_sock),
            "store reset": (_runner("store", "reset", str(host)), host_sock),
            "upgrade": (_runner("migrate", "--dir", str(host)), host_sock),
            "adoption": (_runner("migrate", "--dir", str(adopting)), adopting_sock),
            "init set-aside": (run_runner_init(left, "http://127.0.0.1:9", allow_readd=True), left_sock),
        }

        for verb, (refused, sock) in said.items():
            assert refused.returncode != 0, verb
            assert str(sock) in _said(refused), (verb, _said(refused))
        assert {root: _tree(root) for root in (host, adopting, left)} == trees


def test_a_store_a_configured_db_url_names_is_upgraded_in_place_leaving_data_runner_db_untouched(
    tmp_path: Path,
) -> None:
    root = _host_dir(tmp_path / "host")
    layout = HostLayout(root)
    _migrations(root).downgrade(_PARENT)
    named = tmp_path / "elsewhere.db"
    shutil.move(layout.store_path, named)
    shutil.copyfile(named, layout.legacy_store_path)
    layout.config_path.write_text(f'db_url = "sqlite:///{named}"\n' + layout.config_path.read_text())
    legacy = layout.legacy_store_path.read_bytes()

    upgraded = _runner("migrate", "--dir", str(root))

    assert upgraded.returncode == 0, _said(upgraded)
    assert layout.legacy_store_path.read_bytes() == legacy
    assert not layout.store_path.exists()
    assert _migrations(root).is_current()
