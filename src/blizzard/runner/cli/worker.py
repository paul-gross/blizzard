"""Worker-hook verbs: identity from the spawn-injected environment, no identity arguments."""

from __future__ import annotations

import click

from blizzard.runner.cli.artifact import artifact_create
from blizzard.runner.cli.container_command import ContainerWorkerCommand
from blizzard.runner.cli.wiring import WorkerHook, WorkerOperation
from blizzard.runner.cli.worker_call import WorkerCall, WorkerPolicy, WorkerSession


def send_heartbeat(session: WorkerSession) -> None:
    """Report this worker alive, failing soft: a missing identity or an unreachable runner is said on stderr only."""
    worker = WorkerCall.select(session, WorkerPolicy("heartbeat", soft=True, traced=False))
    if worker is not None:
        worker.soft_post(
            "/api/heartbeat", failure="could not reach the runner", json_body={"lease_id": worker.lease_id}
        )


@click.command(
    cls=ContainerWorkerCommand,
    collaborators={"hook": WorkerHook},
    policy=WorkerPolicy("heartbeat", soft=True, traced=False),
)
def heartbeat(hook: WorkerHook) -> None:
    """Worker hook: report that this worker is still alive (identity from the environment).

    Fails **soft**: a missing identity or an unreachable runner is reported to stderr and this still exits 0."""
    if worker := hook.call:
        worker.soft_post(
            "/api/heartbeat", failure="could not reach the runner", json_body={"lease_id": worker.lease_id}
        )


@click.command(
    "session-end",
    cls=ContainerWorkerCommand,
    collaborators={"hook": WorkerHook},
    policy=WorkerPolicy("session-end", soft=True, skip_elicitation=True),
)
def session_end(hook: WorkerHook) -> None:
    """Worker hook: record the session's exit (identity from the environment) — the worker's
    "declared done" signal.

    Fails **soft**, like the heartbeat."""
    if worker := hook.call:
        worker.soft_post(worker.leased("session-end"), failure="could not reach the runner")


@click.command(cls=ContainerWorkerCommand, collaborators={"worker": WorkerCall}, policy=WorkerPolicy("ask"))
@click.argument("prompt")
@click.option("--options", default=None, help="Pipe-separated answer options.")
def ask(prompt: str, options: str | None, worker: WorkerCall) -> None:
    """Worker: ask a question, then exit — the question is recorded before this returns, and its id printed."""
    body: dict[str, object] = {"question": prompt}
    if options:
        body["options"] = [o for o in options.split("|") if o]
    resp = worker.post(worker.leased("asks"), failure="could not record the question", json_body=body)
    click.echo(resp.json().get("question_id", ""))


@click.group("chunk")
def chunk_group() -> None:
    """Worker: read facts about the chunk this node-step belongs to."""


@chunk_group.command(
    "history", cls=ContainerWorkerCommand, collaborators={"worker": WorkerCall}, policy=WorkerPolicy("chunk history")
)
def chunk_history(worker: WorkerCall) -> None:
    """Worker: read this chunk's own transition history as kind-discriminated JSON — the
    merged, oldest-first timeline, one row per accepted transition, cross-graph migration, delivery
    bounce, gate resolution, hub requeue, or restart, each carrying its own ``kind``. The in-flight
    node-step this call is part of is not there yet: a transition is recorded only once an attempt completes."""
    resp = worker.get(worker.leased("history"), failure="could not read the history")
    click.echo(resp.text)


@chunk_group.command(
    "asks", cls=ContainerWorkerCommand, collaborators={"worker": WorkerCall}, policy=WorkerPolicy("chunk asks")
)
def chunk_asks(worker: WorkerCall) -> None:
    """Worker: read every question asked on this chunk, with its answer, as JSON — oldest-first,
    open and answered alike, each row naming the asking node. A person's answer binds every later
    session on the chunk: read this before asking again, and never contradict an answer."""
    resp = worker.get(worker.leased("asks"), failure="could not read the asks")
    click.echo(resp.text)


@chunk_group.command(
    "notes", cls=ContainerWorkerCommand, collaborators={"worker": WorkerCall}, policy=WorkerPolicy("chunk notes")
)
def chunk_notes(worker: WorkerCall) -> None:
    """Worker: read every note a person left on this chunk resolving a gate, requeueing it, or
    restarting it, as JSON — oldest-first, each row carrying its ``kind`` and who left it. A
    person's note binds every later session on the chunk as an answer does: never contradict one."""
    resp = worker.get(worker.leased("notes"), failure="could not read the notes")
    click.echo(resp.text)


@click.command(
    hidden=True,
    cls=ContainerWorkerCommand,
    collaborators={"operation": WorkerOperation},
    policy=WorkerPolicy("artifact create"),
)
@click.option("--name", required=True, help="The `produces:` name this content is submitted for.")
@click.pass_context
def attach(ctx: click.Context, name: str, operation: WorkerOperation) -> None:
    """Deprecated alias for ``blizzard runner artifact create``.

    Kept working, hidden from ``--help``: it warns on stderr and delegates with identical behavior."""
    click.echo(
        "warning: `blizzard runner attach` is deprecated — use `blizzard runner artifact create`",
        err=True,
    )
    ctx.invoke(artifact_create, name=name, operation=operation)


@click.command(
    "work-items",
    cls=ContainerWorkerCommand,
    collaborators={"worker": WorkerCall},
    policy=WorkerPolicy("work-items", lease=False),
)
@click.argument("chunk_id")
def work_items(chunk_id: str, worker: WorkerCall) -> None:
    """Worker: read a chunk's work items from its tracker, printed as JSON, one entry per pointer."""
    _work_items(worker, chunk_id)


def _work_items(worker: WorkerCall, chunk_id: str) -> None:
    resp = worker.get(f"/api/chunks/{chunk_id}/work-items", failure="could not read the work item")
    click.echo(resp.text)


@click.command(
    "pm-items",
    hidden=True,
    cls=ContainerWorkerCommand,
    collaborators={"operation": WorkerOperation},
    policy=WorkerPolicy("work-items", lease=False),
)
@click.argument("chunk_id")
def pm_items(chunk_id: str, operation: WorkerOperation) -> None:
    """Deprecated alias for ``blizzard runner work-items``.

    Kept working, hidden from ``--help``: it warns on stderr and delegates with identical behavior."""
    click.echo(
        "warning: `blizzard runner pm-items` is deprecated — use `blizzard runner work-items`",
        err=True,
    )
    _work_items(operation.call(), chunk_id)
