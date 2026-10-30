"""``blizzard runner finding`` — the findings the accepted, minted garden proposals
behind a worker's chunk's work items answer."""

from __future__ import annotations

from urllib.parse import quote

import click

from blizzard.runner.cli.worker_call import WorkerCall


@click.group("finding")
def finding_group() -> None:
    """Worker: read the findings the accepted, minted garden proposals behind
    this chunk's work items answer."""


@finding_group.command("list")
def finding_list() -> None:
    """Worker: list the findings the accepted, minted garden proposals behind
    this chunk's work items answer, as JSON."""
    worker = WorkerCall.of("finding list")
    resp = worker.get(worker.leased("findings"), failure="could not read the answered findings")
    click.echo(resp.text)


@finding_group.command("get")
@click.argument("finding_id")
def finding_get(finding_id: str) -> None:
    """Worker: read one finding within the set this chunk's work items answer by FINDING_ID, as
    JSON — a key within the worker's own brief, the way ``artifact get NAME`` is a key
    within its own node-step."""
    worker = WorkerCall.of("finding get")
    resp = worker.get(worker.leased(f"findings/{quote(finding_id, safe='')}"), failure=f"could not read {finding_id!r}")
    click.echo(resp.text)
