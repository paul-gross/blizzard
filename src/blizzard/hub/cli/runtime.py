"""``blizzard hub init``/``migrate``/``host`` — scaffold, migrate, and become the hub runtime."""

from __future__ import annotations

import signal
import types
from functools import partial
from importlib.resources import files
from pathlib import Path

import click

from blizzard import __version__
from blizzard.cli.config_inputs import packaged_flag_registry, strict_config
from blizzard.cli.host_directory import HostDirectory
from blizzard.cli.runtime import build_early_shutdown_server, click_exception_on, run_init, run_migrate
from blizzard.foundation.store.migrations import RevisionMismatchError, StoreBelowFloorError
from blizzard.hub.app import PLATFORM_TRACING_SHUTDOWN_SECONDS, build_hosted_app
from blizzard.hub.config import ConfigError, HubConfig
from blizzard.hub.domain.runners.version_window import VersionWindow, VersionWindowError, check_own_floor
from blizzard.hub.runtime import ensure_current_revision, init_environment, migrate, migration_runner
from blizzard.hub.store import MIGRATIONS_DIR

# The runtime root the dir-taking verbs resolve, highest to lowest: explicit ``--dir``,
# then ``BZ_HUB_DIR``, then the cwd. Selectable, not shareable: the store is single-writer.
ENV_HUB_DIR = "BZ_HUB_DIR"
DEFAULT_DIR = "."

_ALLOW_EXTERNAL_DB_HELP = "Proceed even if the config's db_url names a database outside this directory."

_WINDOW_PACKAGE = "blizzard.wire"
_WINDOW_FILE = "window.toml"


def packaged_version_window() -> VersionWindow:
    """The version window packaged with the wheel; :class:`VersionWindowError` when the file cannot be read."""
    source = files(_WINDOW_PACKAGE).joinpath(_WINDOW_FILE)
    try:
        text = source.read_text()
    except OSError as error:
        raise VersionWindowError(f"{source}: cannot read the window file: {error}") from error
    return VersionWindow.parse(text, str(source))


@click.command()
@click.argument("directory", default=DEFAULT_DIR, envvar=ENV_HUB_DIR)
@click.option("--allow-external-db", "allow_external_db", is_flag=True, default=False, help=_ALLOW_EXTERNAL_DB_HELP)
def init(directory: str, allow_external_db: bool) -> None:
    """Scaffold config + data dir + a migrated store under DIRECTORY. Idempotent.

    DIRECTORY defaults to $BZ_HUB_DIR, then the cwd."""
    run_init(
        Path(directory),
        "hub",
        init_environment=partial(
            init_environment,
            allow_external_db=allow_external_db,
            flag_registry=packaged_flag_registry(),
            strict=strict_config(),
        ),
        migration_runner=migration_runner,
        config_error=ConfigError,
    )


@click.command("migrate")
@click.option(
    "--dir", "directory", default=DEFAULT_DIR, envvar=ENV_HUB_DIR, help="Hub runtime directory (overrides $BZ_HUB_DIR)."
)
@click.option("--down", default=None, help="Reverse migrations down to this revision (e.g. base).")
@click.option("--head", is_flag=True, help="Print this build's hub migration head without opening a store.")
@click.option("--knows", metavar="REV", help="Exit successfully if this build holds REV; print nothing.")
@click.option("--allow-external-db", "allow_external_db", is_flag=True, default=False, help=_ALLOW_EXTERNAL_DB_HELP)
def migrate_cmd(directory: str, down: str | None, head: bool, knows: str | None, allow_external_db: bool) -> None:
    """Apply or reverse migrations, or inspect this build's migration tree."""
    run_migrate(
        Path(directory),
        down,
        head=head,
        knows=knows,
        script_location=MIGRATIONS_DIR,
        migrate=partial(
            migrate, allow_external_db=allow_external_db, flag_registry=packaged_flag_registry(), strict=strict_config()
        ),
        config_error=ConfigError,
    )


@click.command()
@click.argument("directory", required=False, default=None)
@click.option(
    "--dir",
    "dir_option",
    default=DEFAULT_DIR,
    envvar=ENV_HUB_DIR,
    help="Hub runtime directory (overrides $BZ_HUB_DIR).",
)
@click.option("--host", "host_", default=None, help="Bind host (overrides config).")
@click.option("--port", type=int, default=None, help="Bind port (overrides config).")
@click.option("--allow-external-db", "allow_external_db", is_flag=True, default=False, help=_ALLOW_EXTERNAL_DB_HELP)
def host(directory: str | None, dir_option: str, host_: str | None, port: int | None, allow_external_db: bool) -> None:
    """Become the blizzard-hub daemon: HTTP API + SSE + the embedded web app.

    DIRECTORY (positional) and --dir are equivalent — pass one; giving both requires
    they agree. Defaults to $BZ_HUB_DIR, then the cwd."""
    directory = HostDirectory(directory, dir_option).path
    with click_exception_on(VersionWindowError):
        window = packaged_version_window()
        check_own_floor(__version__, window)
    with click_exception_on(ConfigError):
        config = HubConfig.load(
            Path(directory),
            host=host_,
            port=port,
            allow_external_db=allow_external_db,
            flag_registry=packaged_flag_registry(),
            strict=strict_config(),
        )
    with click_exception_on(RevisionMismatchError, StoreBelowFloorError):
        ensure_current_revision(config)
    with click_exception_on(ConfigError):
        app = build_hosted_app(config, version_window=window)
    click.echo(f"serving blizzard-hub on {config.host}:{config.port}")
    server = build_early_shutdown_server(app, host=config.host, port=config.port, shutdown_signal=app.state.shutdown)

    # The server re-raises a caught signal through the handler installed before it ran; the
    # default would kill the process, so this one lets `run()` return and the disposal run.
    def _handle_signal(signum: int, frame: types.FrameType | None) -> None:
        server.handle_exit(signum, frame)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    try:
        server.run()
    finally:
        app.state.engine.dispose()
        app.state.platform_tracing.shutdown(PLATFORM_TRACING_SHUTDOWN_SECONDS)
