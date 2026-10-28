"""Daemon-neutral ``init``/``migrate``/``host`` glue shared by the hub and runner CLIs.

Parameterized, not generalized: each shared body takes the daemon's own
callables/exception types as arguments, so a daemon-specific difference is bound by
the caller, never taught to this module."""

from __future__ import annotations

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


def build_early_shutdown_server(app: Any, *, host: str, port: int, shutdown_signal: Any) -> _EarlyShutdownServer:
    """Wire APP under uvicorn behind the shared early-shutdown wrapper."""
    uvicorn_config = uvicorn.Config(app, host=host, port=port, timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS)
    return _EarlyShutdownServer(uvicorn_config, shutdown_signal=shutdown_signal)
