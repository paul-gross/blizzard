"""The runner's two listeners: the unix socket and the TCP port (unit tier).

D-068 makes filesystem permissions the socket's access control, so the mode is a security
property and is asserted, not assumed. The stale-socket path matters because `kill -9` is
a supported operation: a crashed runner leaves its socket file
behind, and bind() would fail on it forever if nothing cleared it — but a file a *live*
daemon is serving must never be cleared, so the two cases are pulled apart here.
"""

from __future__ import annotations

import socket
import stat
from pathlib import Path

import pytest

from blizzard.runner.config import RunnerConfig
from blizzard.runner.listeners import SOCKET_MODE, ListenerError, bind_listeners, unlink_socket


def _config(tmp_path: Path) -> RunnerConfig:
    """A config over a tmp runtime dir, TCP on an ephemeral port so tests never collide."""
    return RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}", host="127.0.0.1", port=0)


def _close(sockets: list[socket.socket], config: RunnerConfig) -> None:
    for sock in sockets:
        sock.close()
    unlink_socket(config.socket_path)


@pytest.mark.unit
def test_binds_both_a_socket_and_a_tcp_port(tmp_path: Path) -> None:
    """One app, two doors: the CLI's socket and the browser's TCP port (issue #43)."""
    config = _config(tmp_path)
    sockets = bind_listeners(config)
    try:
        families = {sock.family for sock in sockets}
        assert families == {socket.AF_UNIX, socket.AF_INET}
        assert config.socket_path.exists()
    finally:
        _close(sockets, config)


@pytest.mark.unit
def test_socket_is_owner_only(tmp_path: Path) -> None:
    """D-068's access control is the filesystem, so 0600 is the control — not decoration.

    uvicorn's own uds branch would chmod this 0666; binding it ourselves is what avoids
    that, and this is the assertion that keeps it that way.
    """
    config = _config(tmp_path)
    sockets = bind_listeners(config)
    try:
        mode = stat.S_IMODE(config.socket_path.stat().st_mode)
        assert mode == SOCKET_MODE
        assert mode & (stat.S_IRWXG | stat.S_IRWXO) == 0  # nothing for group or other
    finally:
        _close(sockets, config)


@pytest.mark.unit
def test_a_stale_socket_from_a_crash_does_not_block_startup(tmp_path: Path) -> None:
    """`kill -9` leaves the file behind; the next start must not be wedged by a corpse."""
    config = _config(tmp_path)
    config.socket_path.write_text("")  # a file at the path, nothing accepting on it

    sockets = bind_listeners(config)
    try:
        assert config.socket_path.exists()
    finally:
        _close(sockets, config)


@pytest.mark.unit
def test_refuses_to_start_beside_a_live_daemon(tmp_path: Path) -> None:
    """A socket someone is actually serving is not a corpse — the store is single-writer."""
    config = _config(tmp_path)
    first = bind_listeners(config)
    try:
        with pytest.raises(ListenerError, match="already serving"):
            bind_listeners(_config(tmp_path))
    finally:
        _close(first, config)


@pytest.mark.unit
def test_a_tcp_clash_strands_no_socket_file(tmp_path: Path) -> None:
    """Startup fails as a unit: a half-bound daemon would leave a corpse that lies."""
    held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    held.bind(("127.0.0.1", 0))
    held.listen()
    taken = held.getsockname()[1]
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}", host="127.0.0.1", port=taken)
    try:
        with pytest.raises(ListenerError, match="could not bind"):
            bind_listeners(config)
        assert not config.socket_path.exists()
    finally:
        held.close()


@pytest.mark.unit
def test_refuses_a_runtime_dir_too_deep_for_a_unix_socket(tmp_path: Path) -> None:
    """AF_UNIX paths are ~108 bytes; without this the failure is a bare OSError from bind()."""
    deep = tmp_path / ("d" * 60) / ("e" * 60)
    deep.mkdir(parents=True)
    config = RunnerConfig(root=deep, db_url=f"sqlite:///{deep / 'runner.db'}", host="127.0.0.1", port=0)
    with pytest.raises(ListenerError, match="too long for a unix socket"):
        bind_listeners(config)
