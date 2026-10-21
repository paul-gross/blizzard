"""``blizzard runner store reset`` — move the host's store aside and start a fresh one, or extract one runner's rows
into an archive store, while the host is down."""

from __future__ import annotations

from pathlib import Path

import click

from blizzard.cli.config_inputs import packaged_flag_registry, strict_config
from blizzard.cli.operator_trace import ITraceHeaders, OperatorTrace
from blizzard.cli.runtime import click_exception_on
from blizzard.foundation.clock import SystemClock
from blizzard.runner.cli.daemon import daemon_holding
from blizzard.runner.cli.env import DEFAULT_DIR, ENV_RUNNER_DIR
from blizzard.runner.composition import build_runner_store_files
from blizzard.runner.config_table import ConfigError
from blizzard.runner.runtime import Runtime, StoreResetRefused


def runtime_of(
    root: Path, trace: ITraceHeaders, *, hub_url: str | None = None, projects: tuple[str, ...] | None = None
) -> Runtime:
    """The host directory ``root`` administered with the CLI's probe for a pre-host daemon on its ``runner.sock``."""
    return Runtime(
        root,
        hub_url=hub_url,
        projects=projects,
        holder=lambda sock: daemon_holding(sock, trace),
        flag_registry=packaged_flag_registry(),
        strict=strict_config(),
    )


@click.group("store")
def store_group() -> None:
    """Administer the host's local store while the host is down."""


@store_group.command("reset")
@click.argument("directory", default=DEFAULT_DIR, envvar=ENV_RUNNER_DIR)
@click.option(
    "--runner",
    "runner",
    default=None,
    help="Reset only this runner, by name or id: its rows move to an archive store beside the host's.",
)
@click.pass_context
def reset(ctx: click.Context, directory: str, runner: str | None) -> None:
    """Move the store under DIRECTORY aside under a timestamped name, then migrate a fresh one; nothing is deleted.

    With --runner, that runner's rows move to an archive store beside the host's, which `runner ingest` takes back,
    and its outbox counters stay. Refused while the host holds the directory or a pre-host daemon answers on its
    socket, and for a store that is not a SQLite file. DIRECTORY defaults to $BZ_RUNNER_DIR, then the cwd."""
    root = Path(directory)
    runtime = runtime_of(root, OperatorTrace.source(ctx))
    if runner is not None:
        with click_exception_on(ConfigError, StoreResetRefused):
            archived = runtime.reset_runner(runner, now=SystemClock().now(), rows=build_runner_store_files())
        click.echo(f"archived runner {runner} ({archived.runner_id}) to {archived.archive}")
        click.echo(f"its rows are gone from the store at {root}; its outbox counters stay")
        return
    with click_exception_on(ConfigError, StoreResetRefused):
        aside = runtime.reset_store(now=SystemClock().now())
    for path in aside.moved:
        click.echo(f"set aside {path}")
    click.echo(f"runner store at {root} is fresh" + ("" if aside.moved else " (there was none to set aside)"))
