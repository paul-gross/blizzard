"""``blizzard runner finding`` — the findings a worker's own chunk's accepted, minted
garden proposal answers (blizzard#397 Phase 2)."""

from __future__ import annotations

from urllib.parse import quote

import click

from blizzard.runner.cli.worker_call import WorkerCall


@click.group("finding")
def finding_group() -> None:
    """Worker: read the findings this chunk's own accepted, minted garden proposal
    answers.

    The lease binding is ambient, like ``artifact``: every verb acts on the worker's own
    lease, resolved from the spawn environment — no verb takes a flag naming another
    chunk, routine, or scope."""


@finding_group.command("list")
def finding_list() -> None:
    """Worker: list the findings this chunk's own accepted, minted garden proposal
    answers, as JSON."""
    worker = WorkerCall.of("finding list")
    resp = worker.get(worker.leased("findings"), failure="could not read the answered findings")
    click.echo(resp.text)


@finding_group.command("get")
@click.argument("finding_id")
def finding_get(finding_id: str) -> None:
    """Worker: read one finding within this chunk's own answered set by FINDING_ID, as
    JSON — a key within the worker's own brief, the way ``artifact get NAME`` is a key
    within its own node-step."""
    worker = WorkerCall.of("finding get")
    resp = worker.get(worker.leased(f"findings/{quote(finding_id, safe='')}"), failure=f"could not read {finding_id!r}")
    click.echo(resp.text)
