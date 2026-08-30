"""Daemon-neutral ``init``/``migrate``/``host`` glue shared by the hub and runner CLIs.

Parameterized, not generalized (D5): each shared body takes the daemon's own
callables/exception types as arguments, so a daemon-specific difference (e.g. the
hub's ``allow_external_db``) is bound by the caller, never taught to this module."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import click
import uvicorn

from blizzard.foundation.events.server import EarlyShutdownServer as _EarlyShutdownServer

# Bounds uvicorn's own connection-drain wait — defense-in-depth, not the fix for issue #47
# (see ``EarlyShutdownServer``, the shared foundation wrapper, imported above).
GRACEFUL_SHUTDOWN_SECONDS = 5


@contextmanager
def click_exception_on(*error_types: type[BaseException]) -> Iterator[None]:
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
    with click_exception_on(config_error):
        config = init_environment(directory)
    revision = migration_runner(config).current_revision()
    click.echo(f"{daemon_name} runtime ready at {config.root} (store revision {revision})")


def run_migrate(
    directory: Path,
    down: str | None,
    *,
    migrate: Callable[..., None],
    config_error: type[BaseException],
) -> None:
    """The shared ``migrate`` body: apply or reverse via MIGRATE, echo the outcome."""
    with click_exception_on(config_error):
        migrate(directory, down=down)
    click.echo("migrated" if down is None else f"reversed to {down}")


def build_early_shutdown_server(app: Any, *, host: str, port: int, shutdown_signal: Any) -> _EarlyShutdownServer:
    """Wire APP under uvicorn behind the shared early-shutdown wrapper (D1/D3)."""
    uvicorn_config = uvicorn.Config(app, host=host, port=port, timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS)
    return _EarlyShutdownServer(uvicorn_config, shutdown_signal=shutdown_signal)
