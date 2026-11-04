"""Daemon-neutral ``init``/``migrate``/``host`` glue shared by the hub and runner CLIs.

Parameterized, not generalized: each shared body takes the daemon's own
callables/exception types as arguments, so a daemon-specific difference is bound by
the caller, never taught to this module."""

from __future__ import annotations

import os
import socket
import tempfile
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import click
import uvicorn

from blizzard.foundation.events.server import EarlyShutdownServer as _EarlyShutdownServer
from blizzard.foundation.store.migrations import MigrationRunner, StoreBelowFloorError

# Bounds how long shutdown waits for open connections to drain.
GRACEFUL_SHUTDOWN_SECONDS = 5
# How long an idle connection stays open: longer than any client that pools connections reuses one — httpx's 5 s, a
# reverse proxy's 2 min — so none sends a request on a connection the daemon is closing under it.
KEEP_ALIVE_SECONDS = 150


@contextmanager
def click_exception_on(*error_types: type[BaseException]) -> Generator[None, None, None]:
    """Translate any of ERROR_TYPES raised inside the block into a ``click.ClickException``."""
    try:
        yield
    except error_types as exc:
        raise click.ClickException(str(exc)) from exc


def run_init(
    directory: Path,
    daemon_name: str,
    *,
    init_environment: Callable[[Path], Any],
    migration_runner: Callable[[Any], Any],
    config_error: type[BaseException],
) -> None:
    """The shared ``init`` body: scaffold via INIT_ENVIRONMENT, echo DAEMON_NAME's readiness."""
    with click_exception_on(config_error, StoreBelowFloorError):
        config = init_environment(directory)
    revision = migration_runner(config).current_revision()
    click.echo(f"{daemon_name} runtime ready at {config.root} (store revision {revision})")


def run_migrate(
    directory: Path,
    down: str | None,
    *,
    head: bool = False,
    knows: str | None = None,
    script_location: Path,
    migrate: Callable[..., None],
    config_error: type[BaseException],
) -> None:
    """The shared ``migrate`` body: apply or reverse via MIGRATE, echo the outcome."""
    selected = [
        flag for flag, used in (("--head", head), ("--knows", knows is not None), ("--down", down is not None)) if used
    ]
    if len(selected) > 1:
        raise click.UsageError(f"{', '.join(selected)} are mutually exclusive")
    if head or knows is not None:
        tree = MigrationRunner(script_location=script_location, url="sqlite:////dev/null")
        if head:
            click.echo(tree.script_head())
        elif knows is not None and not tree.knows_revision(knows):
            raise SystemExit(1)
        return
    with click_exception_on(config_error, StoreBelowFloorError):
        migrate(directory, down=down)
    click.echo("migrated" if down is None else f"reversed to {down}")


@contextmanager
def bound_tcp(host: str, port: int) -> Generator[socket.socket, None, None]:
    """A listening TCP socket at HOST:PORT, closed when the block exits. Port ``0`` lets the kernel pick one;
    read the port actually bound from :func:`bound_port`. A bind failure is a ``click.ClickException``."""
    sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
        sock.listen()
    except OSError as exc:
        sock.close()
        raise click.ClickException(f"could not bind {host}:{port}: {exc}") from exc
    with sock:
        yield sock


def bound_port(sock: socket.socket) -> int:
    """The port SOCK is bound to."""
    return int(sock.getsockname()[1])


def publish_listening_file(path: Path, host: str, port: int) -> None:
    """Write ``http://HOST:PORT`` and a newline to PATH atomically: a temp file in PATH's directory, then
    ``os.replace``, so a reader sees no file or the whole URL — never part of one. A missing directory is created;
    the file is world-readable, as any daemon's published address is."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(f"http://{host}:{port}\n")
        os.chmod(temp, 0o644)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


class _ListeningServer(_EarlyShutdownServer):
    """Publishes the listening file once uvicorn's startup has completed — the daemon's readiness signal."""

    def __init__(
        self, config: uvicorn.Config, *, shutdown_signal: Any, listening_file: Path | None, host: str, port: int
    ) -> None:
        super().__init__(config, shutdown_signal=shutdown_signal)
        self._listening_file = listening_file
        self._host = host
        self._port = port

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets=sockets)
        if self.started and self._listening_file is not None:
            publish_listening_file(self._listening_file, self._host, self._port)


def build_early_shutdown_server(
    app: Any,
    *,
    host: str,
    port: int,
    shutdown_signal: Any,
    listening_file: Path | None = None,
) -> _EarlyShutdownServer:
    """Wire APP under uvicorn behind the shared early-shutdown wrapper. PORT is the port the daemon serves on —
    the bound one, when it is handed pre-bound sockets through ``run(sockets=...)``. LISTENING_FILE, when given,
    receives ``http://HOST:PORT`` once startup has completed."""
    uvicorn_config = uvicorn.Config(
        app,
        host=host,
        port=port,
        timeout_keep_alive=KEEP_ALIVE_SECONDS,
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
    )
    return _ListeningServer(
        uvicorn_config, shutdown_signal=shutdown_signal, listening_file=listening_file, host=host, port=port
    )


def listening_file_option(function: Callable[..., Any]) -> Callable[..., Any]:
    """The ``--listening-file PATH`` option a daemon's ``host`` verb takes."""
    return click.option(
        "--listening-file",
        "listening_file",
        type=click.Path(dir_okay=False, path_type=Path),
        default=None,
        help="Once serving, write the daemon's URL (http://HOST:PORT, the port actually bound) to this file, "
        "atomically. Pair with --port 0 to let the kernel pick a free port.",
    )(function)
