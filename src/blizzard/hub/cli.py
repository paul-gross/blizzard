"""``blizzard hub <cmd>`` — the fleet surface (design/cli.md).

Client verbs are pure clients of the hub's HTTP API; ``host`` *becomes* the hub
daemon (D-061). Only ``init`` / ``migrate`` / ``host`` are implemented in the
scaffold — the rest are stubs that name themselves, present in ``--help`` and
filled in by the backend builder. This module is CLI top-level glue, so ``echo``
for user output is fine here (``bzh:structlog-logging``); diagnostics go through
structlog inside the runtime and app.
"""

from __future__ import annotations

from pathlib import Path

import click
import uvicorn

from blizzard.foundation.store.migrations import RevisionMismatchError
from blizzard.hub.app import create_app
from blizzard.hub.config import ConfigError, HubConfig
from blizzard.hub.runtime import ensure_current_revision, init_environment, migrate, migration_runner


def _stub(verb: str) -> None:
    raise click.ClickException(f"`blizzard hub {verb}` is not yet implemented (scaffold stub).")


@click.group(invoke_without_command=True)
@click.pass_context
def hub(ctx: click.Context) -> None:
    """Talk to — or become — the blizzard hub."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(host)


@hub.command()
@click.argument("directory", default=".")
def init(directory: str) -> None:
    """Scaffold config + data dir + a migrated store under DIRECTORY. Idempotent."""
    config = init_environment(Path(directory))
    revision = migration_runner(config).current_revision()
    click.echo(f"hub runtime ready at {config.root} (store revision {revision})")


@hub.command("migrate")
@click.option("--dir", "directory", default=".", help="Hub runtime directory.")
@click.option("--down", default=None, help="Reverse migrations down to this revision (e.g. base).")
def migrate_cmd(directory: str, down: str | None) -> None:
    """Apply pending store migrations, or reverse with --down <rev>."""
    try:
        migrate(Path(directory), down=down)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo("migrated" if down is None else f"reversed to {down}")


@hub.command()
@click.option("--dir", "directory", default=".", help="Hub runtime directory.")
@click.option("--host", "host_", default=None, help="Bind host (overrides config).")
@click.option("--port", type=int, default=None, help="Bind port (overrides config).")
def host(directory: str, host_: str | None, port: int | None) -> None:
    """Become the blizzard-hub daemon: HTTP API + SSE + the embedded web app."""
    try:
        config = HubConfig.load(Path(directory), host=host_, port=port)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        ensure_current_revision(config)
    except RevisionMismatchError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"serving blizzard-hub on {config.host}:{config.port}")
    uvicorn.run(create_app(config), host=config.host, port=config.port)


@hub.command()
def status() -> None:
    """The fleet view: every chunk, open question, and registered runner."""
    _stub("status")


@hub.command()
@click.argument("question_id")
@click.argument("answer")
def answer(question_id: str, answer: str) -> None:
    """Answer an open question (first-write-wins CAS at the hub)."""
    _stub("answer")


@hub.command()
def decisions() -> None:
    """List open decisions (gate surfacing)."""
    _stub("decisions")


@hub.command()
@click.argument("decision_id")
@click.argument("choice")
def decide(decision_id: str, choice: str) -> None:
    """Resolve an open decision (first-write-wins)."""
    _stub("decide")


@hub.command()
def ingest() -> None:
    """Ingest PM items by pointer, minting chunks."""
    _stub("ingest")


@hub.command()
@click.argument("chunk_id")
def detach(chunk_id: str) -> None:
    """Forcibly release a chunk from its runner (D-088)."""
    _stub("detach")
