"""The runner daemon's listeners: a unix socket and a TCP port over the one ASGI app (issue #43).

D-068 settles the local API on **HTTP over a unix domain socket** — the socket under the
state dir, filesystem permissions as access control — with localhost TCP as an opt-in. The
socket is what the CLI's local verbs address (found from the runtime dir alone, no port
registry, and no listening port for anything to push into — D-012). TCP is what the
browser addresses, because a browser cannot open a unix socket and the runner serves its
web app same-origin with ``/api/*`` ([web-app.md]). Both are the *same* app and the same
route table — two doors, not two APIs (D-068: "HTTP semantics keep this route table as
written").

Today both listeners are always on: TCP is not yet the opt-in D-068 describes, because the
worker hooks still inherit ``BLIZZARD_RUNNER_URL`` as a TCP URL. Making TCP opt-in is its
own change; this module exists so the socket lands without disturbing anything speaking TCP.

Sockets are bound **here** rather than handed to uvicorn as ``Config(uds=…)`` for two
reasons: uvicorn's own uds branch chmods the socket ``0o666``, which would leave D-068's
"filesystem permissions as access control" resting entirely on the parent directory; and
pre-bound sockets let one ``uvicorn.Server`` serve both (``run(sockets=[...])`` creates a
listener per socket under a single ``should_exit``), which keeps the daemon's one-server
shutdown — and the D-082 resume-marking that runs after it returns — undisturbed.
"""

from __future__ import annotations

import socket
from pathlib import Path

import structlog

from blizzard.runner.config import RunnerConfig

_log = structlog.get_logger(__name__)

# The socket's own mode: owner-only. D-068 makes filesystem permissions the access control,
# and the containing state dir is only as tight as systemd's StateDirectoryMode (0750 by
# default) — which would still admit the whole group. 0600 pins it to the daemon's user.
SOCKET_MODE = 0o600
# AF_UNIX paths are bounded by the kernel's sockaddr_un (108 bytes on Linux, including the
# terminator); a deep runtime dir would otherwise fail inside bind() with a bare OSError.
_MAX_SOCKET_PATH = 107


class ListenerError(RuntimeError):
    """A listener could not be bound — the daemon must not start."""


def bind_listeners(config: RunnerConfig) -> list[socket.socket]:
    """Bind the local socket and the TCP port, for ``uvicorn.Server.run(sockets=...)``.

    Both are bound before the daemon starts serving, so a port clash or a live sibling on
    the socket fails startup loudly rather than half-serving.
    """
    uds = _bind_uds(config.socket_path)
    try:
        tcp = _bind_tcp(config.host, config.port)
    except ListenerError:
        uds.close()  # don't strand a bound socket (or its file) when TCP is the failure
        unlink_socket(config.socket_path)
        raise
    return [uds, tcp]


def unlink_socket(path: Path) -> None:
    """Remove the socket file — uvicorn does not, for a socket it was handed pre-bound."""
    path.unlink(missing_ok=True)


def _bind_uds(path: Path) -> socket.socket:
    if len(str(path)) > _MAX_SOCKET_PATH:
        raise ListenerError(
            f"runtime dir path is too long for a unix socket at {path} "
            f"({len(str(path))} > {_MAX_SOCKET_PATH} bytes) — use a shorter --dir"
        )
    _clear_stale_socket(path)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.bind(str(path))
        # chmod after bind, before listen: the socket exists but is not yet accepting, so
        # there is no window where it is reachable at a laxer mode.
        path.chmod(SOCKET_MODE)
        sock.listen()
    except OSError as exc:
        sock.close()
        raise ListenerError(f"could not bind the runner socket at {path}: {exc}") from exc
    _log.info("runner socket bound", path=str(path), mode=oct(SOCKET_MODE))
    return sock


def _clear_stale_socket(path: Path) -> None:
    """Remove a socket file left by a crash — but never one a live daemon is serving.

    A `kill -9` leaves the file behind and bind() would then fail with EADDRINUSE, so a
    crashed runner could never restart. The discriminator is whether anything is actually
    accepting on it: a refused connection means the file is a corpse.
    """
    if not path.exists():
        return
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.connect(str(path))
    except OSError:
        _log.info("clearing stale runner socket", path=str(path))
        path.unlink(missing_ok=True)
        return
    finally:
        probe.close()
    raise ListenerError(
        f"a runner daemon is already serving on {path} — stop it before starting another (the store is single-writer)"
    )


def _bind_tcp(host: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
        sock.listen()
    except OSError as exc:
        sock.close()
        raise ListenerError(f"could not bind {host}:{port}: {exc}") from exc
    return sock
