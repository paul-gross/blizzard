"""``blizzard runner scope`` — the deployment's scope vocabulary (blizzard#582 D2)."""

from __future__ import annotations

import click

from blizzard.runner.cli.worker_call import WorkerCall


@click.group("scope")
def scope_group() -> None:
    """Worker: read the deployment's scope vocabulary."""


@scope_group.command("list")
def scope_list() -> None:
    """Worker: list every scope (slug, description, retired) as JSON."""
    worker = WorkerCall.of("scope list")
    resp = worker.get(worker.leased("scopes"), failure="could not read the scope list")
    click.echo(resp.text)
