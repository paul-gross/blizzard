"""``blizzard hub init``/``migrate``/``host`` — scaffold, migrate, and become the hub runtime."""

from __future__ import annotations

from functools import partial
from pathlib import Path

import click

from blizzard.cli.host_directory import HostDirectory
from blizzard.cli.runtime import build_early_shutdown_server, click_exception_on, run_init, run_migrate
from blizzard.foundation.store.migrations import RevisionMismatchError
from blizzard.hub.app import build_hosted_app
from blizzard.hub.config import ConfigError, HubConfig
from blizzard.hub.runtime import ensure_current_revision, init_environment, migrate, migration_runner

# The runtime root the dir-taking verbs resolve, highest to lowest: explicit ``--dir``,
# then ``BZ_HUB_DIR``, then the cwd. Selectable, not shareable: the store is single-writer.
ENV_HUB_DIR = "BZ_HUB_DIR"
DEFAULT_DIR = "."

_ALLOW_EXTERNAL_DB_HELP = (
    "Proceed even if the config's db_url names a database outside this directory (issue #234's --dir isolation guard)."
)


@click.command()
@click.argument("directory", default=DEFAULT_DIR, envvar=ENV_HUB_DIR)
@click.option("--allow-external-db", "allow_external_db", is_flag=True, default=False, help=_ALLOW_EXTERNAL_DB_HELP)
def init(directory: str, allow_external_db: bool) -> None:
    """Scaffold config + data dir + a migrated store under DIRECTORY. Idempotent.

    DIRECTORY defaults to $BZ_HUB_DIR, then the cwd."""
    run_init(
        Path(directory),
        "hub",
        init_environment=partial(init_environment, allow_external_db=allow_external_db),
        migration_runner=migration_runner,
        config_error=ConfigError,
    )


@click.command("migrate")
@click.option(
    "--dir", "directory", default=DEFAULT_DIR, envvar=ENV_HUB_DIR, help="Hub runtime directory (overrides $BZ_HUB_DIR)."
)
@click.option("--down", default=None, help="Reverse migrations down to this revision (e.g. base).")
@click.option("--allow-external-db", "allow_external_db", is_flag=True, default=False, help=_ALLOW_EXTERNAL_DB_HELP)
def migrate_cmd(directory: str, down: str | None, allow_external_db: bool) -> None:
    """Apply pending store migrations, or reverse with --down <rev>."""
    run_migrate(
        Path(directory),
        down,
        migrate=partial(migrate, allow_external_db=allow_external_db),
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
    with click_exception_on(ConfigError):
        config = HubConfig.load(Path(directory), host=host_, port=port, allow_external_db=allow_external_db)
    with click_exception_on(RevisionMismatchError):
        ensure_current_revision(config)
    # Composition can still reject the config at boot; surface it as a clean CLI error,
    # and build before announcing so we never claim to serve and then die.
    with click_exception_on(ConfigError):
        app = build_hosted_app(config)
    click.echo(f"serving blizzard-hub on {config.host}:{config.port}")
    build_early_shutdown_server(app, host=config.host, port=config.port, shutdown_signal=app.state.shutdown).run()
