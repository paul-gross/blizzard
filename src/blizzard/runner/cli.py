"""``blizzard runner <cmd>`` — the machine-local surface (design/cli.md).

Client verbs are pure clients of the runner's local API; ``host`` *becomes* the
runner daemon (D-061). Only ``init`` / ``migrate`` / ``host`` are implemented in
the scaffold — the rest are stubs that name themselves, present in ``--help`` and
filled in by the backend builder. Worker-hook verbs (``heartbeat``, ``ask``,
``pm-items``) take their identity from the spawn-injected environment and pass no
identity arguments.
"""

from __future__ import annotations

from pathlib import Path

import click
import uvicorn

from blizzard.foundation.store.migrations import RevisionMismatchError
from blizzard.runner.app import create_app
from blizzard.runner.config import ConfigError, RunnerConfig
from blizzard.runner.runtime import ensure_current_revision, init_environment, migrate, migration_runner


def _stub(verb: str) -> None:
    raise click.ClickException(f"`blizzard runner {verb}` is not yet implemented (scaffold stub).")


@click.group(invoke_without_command=True)
@click.pass_context
def runner(ctx: click.Context) -> None:
    """Talk to — or become — the blizzard runner."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(host)


@runner.command()
@click.argument("directory", default=".")
def init(directory: str) -> None:
    """Scaffold config + data dir + a migrated store under DIRECTORY. Idempotent."""
    config = init_environment(Path(directory))
    revision = migration_runner(config).current_revision()
    click.echo(f"runner runtime ready at {config.root} (store revision {revision})")


@runner.command("migrate")
@click.option("--dir", "directory", default=".", help="Runner runtime directory.")
@click.option("--down", default=None, help="Reverse migrations down to this revision (e.g. base).")
def migrate_cmd(directory: str, down: str | None) -> None:
    """Apply pending store migrations, or reverse with --down <rev>."""
    try:
        migrate(Path(directory), down=down)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo("migrated" if down is None else f"reversed to {down}")


@runner.command()
@click.option("--dir", "directory", default=".", help="Runner runtime directory.")
@click.option("--host", "host_", default=None, help="Bind host (overrides config).")
@click.option("--port", type=int, default=None, help="Bind port (overrides config).")
def host(directory: str, host_: str | None, port: int | None) -> None:
    """Become the blizzard-runner daemon: the reconciliation loop + the local API."""
    try:
        config = RunnerConfig.load(Path(directory), host=host_, port=port)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        ensure_current_revision(config)
    except RevisionMismatchError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"serving blizzard-runner on {config.host}:{config.port}")
    uvicorn.run(create_app(config), host=config.host, port=config.port)


@runner.command()
def heartbeat() -> None:
    """Worker hook: record a lease heartbeat (identity from the environment)."""
    _stub("heartbeat")


@runner.command()
@click.argument("prompt")
@click.option("--options", default=None, help="Pipe-separated answer options.")
def ask(prompt: str, options: str | None) -> None:
    """Worker: ask-and-exit; the ask fact is durable before the worker exits."""
    _stub("ask")


@runner.command("pm-items")
@click.argument("chunk_id")
def pm_items(chunk_id: str) -> None:
    """Worker: pass-through read of a chunk's PM items (runner -> hub -> vendor)."""
    _stub("pm-items")


@runner.command()
def status() -> None:
    """The machine-local view: capacities, held environments, open asks, escalations."""
    _stub("status")


@runner.command()
def pause() -> None:
    """Declarative control: pause the runner."""
    _stub("pause")


@runner.command()
def start() -> None:
    """Declarative control: resume the runner."""
    _stub("start")


@runner.command()
@click.argument("chunk_id")
def takeover(chunk_id: str) -> None:
    """Take over a parked chunk, returning the interactive resume command."""
    _stub("takeover")


@runner.command()
@click.argument("chunk_id")
def requeue(chunk_id: str) -> None:
    """Hand a taken-over chunk back to the fleet."""
    _stub("requeue")


@runner.command()
@click.argument("coding_harness")
def selftest(coding_harness: str) -> None:
    """Adapter-drift canary before an unattended period."""
    _stub("selftest")
