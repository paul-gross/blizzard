"""The runner daemon's listeners: a unix socket and a TCP port over the one ASGI app (#43).

Sockets are bound here rather than via ``Config(uds=…)``: uvicorn's uds branch chmods the
socket ``0o666``, losing the owner-only mode filesystem access control rests on.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from pathlib import Path

import structlog

from blizzard.runner.config import RunnerConfig

_log = structlog.get_logger(__name__)

# Owner-only: the containing state dir may be group-readable, so this pins the socket to the daemon's user.
SOCKET_MODE = 0o600
# The kernel's sockaddr_un bound on Linux (108 bytes, terminator included).
_MAX_SOCKET_PATH = 107


class ListenerError(RuntimeError):
    """A listener could not be bound — the daemon must not start."""


@dataclass(frozen=True)
class Uds:
    """The unix-domain listener: a socket file at ``path``, bound at ``SOCKET_MODE``."""

    path: Path

    def bound(self) -> socket.socket:
        if len(str(self.path)) > _MAX_SOCKET_PATH:
            raise ListenerError(
                f"runtime dir path is too long for a unix socket at {self.path} "
                f"({len(str(self.path))} > {_MAX_SOCKET_PATH} bytes) — use a shorter --dir"
            )
        self._clear()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind(str(self.path))
            # chmod after bind, before listen: the socket exists but is not yet accepting, so
            # there is no window where it is reachable at a laxer mode.
            self.path.chmod(SOCKET_MODE)
            sock.listen()
        except OSError as exc:
            sock.close()
            raise ListenerError(f"could not bind the runner socket at {self.path}: {exc}") from exc
        _log.info("runner socket bound", path=str(self.path), mode=oct(SOCKET_MODE))
        return sock

    def unlink(self) -> None:
        """Remove the socket file — uvicorn does not, for a socket it was handed pre-bound."""
        self.path.unlink(missing_ok=True)

    @property
    def stale(self) -> bool:
        """True when nothing is accepting on the file — the corpse a `kill -9` leaves, which
        bind() would otherwise fail on forever with EADDRINUSE."""
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.connect(str(self.path))
        except OSError:
            return True
        finally:
            probe.close()
        return False

    def _clear(self) -> None:
        """Remove a stale socket file — but never one a live daemon is serving."""
        if not self.path.exists():
            return
        if not self.stale:
            raise ListenerError(
                f"a runner daemon is already serving on {self.path} — "
                "stop it before starting another (the store is single-writer)"
            )
        _log.info("clearing stale runner socket", path=str(self.path))
        self.unlink()


@dataclass(frozen=True)
class Tcp:
    """The TCP listener at ``host``:``port``."""

    host: str
    port: int

    def bound(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.host, self.port))
            sock.listen()
        except OSError as exc:
            sock.close()
            raise ListenerError(f"could not bind {self.host}:{self.port}: {exc}") from exc
        return sock


@dataclass(frozen=True)
class Listeners:
    """Both always-on doors onto the one ASGI app, over the same route table."""

    uds: Uds
    tcp: Tcp

    @classmethod
    def of(cls, config: RunnerConfig) -> Listeners:
        return cls(Uds(config.socket_path), Tcp(config.host, config.port))

    def bound(self) -> list[socket.socket]:
        """Bind both for ``uvicorn.Server.run(sockets=...)``, before the daemon serves — so a
        port clash or a live sibling on the socket fails startup loudly, not half-served."""
        uds = self.uds.bound()
        try:
            tcp = self.tcp.bound()
        except ListenerError:
            uds.close()  # don't strand a bound socket (or its file) when TCP is the failure
            self.uds.unlink()
            raise
        return [uds, tcp]


def bind_listeners(config: RunnerConfig) -> list[socket.socket]:
    return Listeners.of(config).bound()


def unlink_socket(path: Path) -> None:
    Uds(path).unlink()
