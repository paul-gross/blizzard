"""``blizzard hub init``/``migrate``/``host`` — scaffold, migrate, and become the hub runtime."""

from __future__ import annotations

import os
import signal
import types
from dataclasses import replace
from functools import partial
from importlib.resources import files
from pathlib import Path

import click

from blizzard import __version__
from blizzard.cli.config_inputs import packaged_flag_registry, strict_config
from blizzard.cli.host_directory import HostDirectory
from blizzard.cli.runtime import (
    bound_port,
    bound_tcp,
    build_early_shutdown_server,
    click_exception_on,
    listening_file_option,
    run_init,
    run_migrate,
)
from blizzard.foundation.logging import get_logger
from blizzard.foundation.settings_record.refusals import SettingsInvalid
from blizzard.foundation.store.migrations import RevisionMismatchError, StoreBelowFloorError
from blizzard.hub.app import PLATFORM_TRACING_SHUTDOWN_SECONDS, build_hosted_app, open_hub_engine
from blizzard.hub.composition import HubSettingsGraph, build_hub_settings, build_system_clock
from blizzard.hub.config import ConfigError, HubConfig, bootstrap_document
from blizzard.hub.config_keys import HUB_REGISTRY
from blizzard.hub.domain.runners.version_window import VersionWindow, VersionWindowError, check_own_floor
from blizzard.hub.runtime import ensure_current_revision, init_environment, migrate, migration_runner
from blizzard.hub.settings.footing import HubSettingsFooting, setting_keys
from blizzard.hub.store import MIGRATIONS_DIR
from blizzard.hub.store.errors import HubStoreConnections, HubStoreErrorFactory

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
@listening_file_option
@click.option("--allow-external-db", "allow_external_db", is_flag=True, default=False, help=_ALLOW_EXTERNAL_DB_HELP)
def host(
    directory: str | None,
    dir_option: str,
    host_: str | None,
    port: int | None,
    listening_file: Path | None,
    allow_external_db: bool,
) -> None:
    """Become the blizzard-hub daemon: HTTP API + SSE + the embedded web app.

    DIRECTORY (positional) and --dir are equivalent — pass one; giving both requires
    they agree. Defaults to $BZ_HUB_DIR, then the cwd."""
    directory = HostDirectory(directory, dir_option).path
    with click_exception_on(VersionWindowError):
        window = packaged_version_window()
        check_own_floor(__version__, window)
    environ = dict(os.environ)
    flag_registry = packaged_flag_registry()
    strict = strict_config()
    with click_exception_on(ConfigError):
        document = HubConfig.read_document(Path(directory))
        config = HubConfig.load(
            Path(directory),
            host=host_,
            port=port,
            allow_external_db=allow_external_db,
            flag_registry=flag_registry,
            strict=strict,
            document=bootstrap_document(document),
            environ=environ,
        )
    with click_exception_on(RevisionMismatchError, StoreBelowFloorError):
        ensure_current_revision(config)
    footing = HubSettingsFooting(
        config.root,
        host_,
        port,
        allow_external_db,
        flag_registry,
        strict,
        HUB_REGISTRY,
        environ,
        config.db_url,
        document,
    )
    settings_engine = open_hub_engine(config.db_url)
    settings = build_hub_settings(
        HubStoreConnections(settings_engine, HubStoreErrorFactory(get_logger("blizzard.hub.store"))),
        build_system_clock(),
        footing,
    )
    try:
        with click_exception_on(ConfigError, SettingsInvalid):
            settings.store.seed(setting_keys(HUB_REGISTRY), footing.file_view(document))
            config = footing.resolve(settings.reader.snapshot().entries)
        _serve(config, window, listening_file, settings)
    finally:
        settings_engine.dispose()


def _serve(config: HubConfig, window: VersionWindow, listening_file: Path | None, settings: HubSettingsGraph) -> None:
    # Bound before anything is built, so every derivation of the port sees the one actually bound.
    with bound_tcp(config.host, config.port) as sock:
        config = replace(config, port=bound_port(sock))
        with click_exception_on(ConfigError):
            app = build_hosted_app(config, version_window=window, settings=settings)
        try:
            click.echo(f"serving blizzard-hub on {config.host}:{config.port}")
            server = build_early_shutdown_server(
                app,
                host=config.host,
                port=config.port,
                shutdown_signal=app.state.shutdown,
                listening_file=listening_file,
            )

            # The server re-raises a caught signal through the handler installed before it ran; the
            # default would kill the process, so this one lets `run()` return and the disposal run.
            def _handle_signal(signum: int, frame: types.FrameType | None) -> None:
                server.handle_exit(signum, frame)

            signal.signal(signal.SIGTERM, _handle_signal)
            signal.signal(signal.SIGINT, _handle_signal)
            server.run(sockets=[sock])
        finally:
            # Lifespan normally closes the hosted resources; cover servers that never enter it too.
            try:
                app.state.hosted_cleanup.close()
            finally:
                app.state.platform_tracing.shutdown(PLATFORM_TRACING_SHUTDOWN_SECONDS)
