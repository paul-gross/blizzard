"""``blizzard runner store reset`` — move the runner's store aside and start a fresh one, while its
daemon is down."""

from __future__ import annotations

from pathlib import Path

import click

from blizzard.cli.operator_trace import ITraceHeaders, OperatorTrace
from blizzard.cli.runtime import click_exception_on
from blizzard.foundation.clock import SystemClock
from blizzard.runner.cli.daemon import daemon_holding
from blizzard.runner.cli.env import DEFAULT_DIR, ENV_RUNNER_DIR
from blizzard.runner.config import ConfigError
from blizzard.runner.runtime import Runtime, StoreResetRefused


def refuse_while_held(root: Path, trace: ITraceHeaders, *, action: str) -> None:
    """Stop ``action`` while anything holds ``root``'s socket: renaming a database a live daemon
    holds open loses whatever it writes next."""
    holding = daemon_holding(root, trace)
    if holding is not None:
        raise click.ClickException(f"{holding}, so this cannot {action} — stop it first, then re-run")


@click.group("store")
def store_group() -> None:
    """Administer the runner's local store while its daemon is down."""


@store_group.command("reset")
@click.argument("directory", default=DEFAULT_DIR, envvar=ENV_RUNNER_DIR)
@click.pass_context
def reset(ctx: click.Context, directory: str) -> None:
    """Move the store under DIRECTORY aside under a timestamped name, then migrate a fresh one.

    Nothing is deleted: the old store keeps its undelivered events and in-flight leases beside the
    new one. Refused while the runner's daemon answers on its socket, and for a store that is not a
    SQLite file. DIRECTORY defaults to $BZ_RUNNER_DIR, then the cwd."""
    root = Path(directory)
    refuse_while_held(root, OperatorTrace.source(ctx), action="move the runner store aside")
    with click_exception_on(ConfigError, StoreResetRefused):
        aside = Runtime(root).reset_store(now=SystemClock().now())
    for path in aside.moved:
        click.echo(f"set aside {path}")
    click.echo(f"runner store at {root} is fresh" + ("" if aside.moved else " (there was none to set aside)"))
