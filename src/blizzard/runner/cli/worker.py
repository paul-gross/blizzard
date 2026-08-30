"""Worker-hook verbs: identity from the spawn-injected environment, no identity arguments."""

from __future__ import annotations

import os

import click

from blizzard.runner.cli.artifact import artifact_create
from blizzard.runner.cli.worker_call import ENV_ELICITATION, WorkerCall


@click.command()
def heartbeat() -> None:
    """Worker hook: record a lease heartbeat (identity from the environment).

    Fails **soft**: a hook must never break the worker's tool call, so a missing identity or an
    unreachable runner is reported to stderr and this still exits 0."""
    worker = WorkerCall.hook("heartbeat")
    if worker is not None:
        worker.soft_post(
            "/api/heartbeat", failure="could not reach the runner", json_body={"lease_id": worker.lease_id}
        )


@click.command("session-end")
def session_end() -> None:
    """Worker hook: record the session's exit (identity from the environment) — the worker's
    "declared done" signal.

    Fails **soft**, like the heartbeat: a hook must never break the worker's exit, so a failure
    is reported to stderr and this still exits 0."""
    if os.environ.get(ENV_ELICITATION):
        return
    worker = WorkerCall.hook("session-end")
    if worker is not None:
        worker.soft_post(worker.leased("session-end"), failure="could not reach the runner")


@click.command()
@click.argument("prompt")
@click.option("--options", default=None, help="Pipe-separated answer options.")
def ask(prompt: str, options: str | None) -> None:
    """Worker: ask-and-exit — the ask is a durable runner-store fact before this returns."""
    worker = WorkerCall.of("ask")
    body: dict[str, object] = {"question": prompt}
    if options:
        body["options"] = [o for o in options.split("|") if o]
    resp = worker.post(worker.leased("asks"), failure="could not record the question", json_body=body)
    click.echo(resp.json().get("question_id", ""))


@click.group("chunk")
def chunk_group() -> None:
    """Worker: read facts about the chunk this node-step belongs to.

    The lease binding is ambient, like ``artifact``: every verb in this group acts on the worker's
    own lease, resolved from the spawn environment, so none takes a flag by which a worker could
    name another chunk."""


@chunk_group.command("history")
def chunk_history() -> None:
    """Worker: read this chunk's own transition history as kind-discriminated JSON (issue #237) — the
    merged, oldest-first timeline, one row per accepted transition, cross-graph migration, or delivery
    bounce, each carrying its own ``kind``. The in-flight node-step this call is part of is not
    there yet: a transition is recorded only once an attempt completes."""
    worker = WorkerCall.of("chunk history")
    resp = worker.get(worker.leased("history"), failure="could not read the history")
    click.echo(resp.text)


@click.command(hidden=True)
@click.option("--name", required=True, help="The `produces:` name this content is submitted for.")
@click.pass_context
def attach(ctx: click.Context, name: str) -> None:
    """Deprecated alias for ``blizzard runner artifact create`` (issue #127).

    Kept working, hidden from ``--help``: it warns on stderr and delegates with identical behavior."""
    click.echo(
        "warning: `blizzard runner attach` is deprecated — use `blizzard runner artifact create`",
        err=True,
    )
    ctx.invoke(artifact_create, name=name)


@click.command("work-items")
@click.argument("chunk_id")
def work_items(chunk_id: str) -> None:
    """Worker: pass-through read of a chunk's work items (runner -> hub -> vendor).

    The items print as JSON, one entry per pointer."""
    worker = WorkerCall.of("work-items", lease=False)
    resp = worker.get(f"/api/chunks/{chunk_id}/work-items", failure="could not read the work item")
    click.echo(resp.text)


@click.command("pm-items", hidden=True)
@click.argument("chunk_id")
@click.pass_context
def pm_items(ctx: click.Context, chunk_id: str) -> None:
    """Deprecated alias for ``blizzard runner work-items`` (issue #55).

    Kept working, hidden from ``--help``: a node's prompt is inlined into the store at mint and
    immutable thereafter, so every already-minted graph names this verb forever (pinned by
    tests/test_pin_runner_misc.py::test_the_deprecated_pm_items_cli_alias_still_reads_the_work_item)."""
    click.echo(
        "warning: `blizzard runner pm-items` is deprecated — use `blizzard runner work-items`",
        err=True,
    )
    ctx.invoke(work_items, chunk_id=chunk_id)
