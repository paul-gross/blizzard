"""``blizzard hub chunk`` — operator commands over one chunk."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, TypedDict, cast

import click

from blizzard.foundation.chunk_status import TERMINAL_STATUSES
from blizzard.foundation.roles import dto
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.project_option import project_option, project_query
from blizzard.hub.cli.views import ChunkLine, Cost, Listing, pointer_label


class Neighbor(TypedDict):
    chunk_id: str
    satisfied: bool


class Neighborhood(TypedDict, total=False):
    prerequisites: list[Neighbor]
    dependents: list[Neighbor]


class ChunkListing(Listing):
    empty = "no chunks"

    def line(self, row: Any) -> str:
        return ChunkLine(row).line()


@dto
@dataclass(frozen=True)
class ChunkDetail:
    body: dict[str, Any]

    def lines(self) -> Iterator[str]:
        body = self.body
        yield f"{body['chunk_id']}  status={body['status']}  graph={body.get('graph_name') or body['graph_id']}"
        yield f"  node: {ChunkLine(body).node}"
        # Both defaults on their own line: `chunk set` can write either, so a text-mode
        # read-back exists for both. `-` is "express no preference", not unknown.
        models = ", ".join(body.get("default_model") or []) or "-"
        yield f"  default model: {models}   default effort: {body.get('default_effort') or '-'}"
        harnesses = ", ".join(body.get("default_harnesses") or []) or "-"
        yield f"  default harnesses: {harnesses}"
        pointers = cast(list[dict[str, object]], body.get("work_refs") or [])
        if pointers:
            labels = ", ".join(pointer_label(p) for p in pointers)
            yield f"  pointers: {labels}"
        route = body.get("route")
        # A done or stopped chunk can still carry a route; no runner holds it.
        if route and body["status"] not in TERMINAL_STATUSES:
            yield f"  runner: {route['runner_id']}  environments: {len(route.get('environment_ids', []))}"
        blocked = body.get("blocked")
        if blocked:
            yield f"  blocked: waiting on {blocked['prerequisite_chunk_id']}"
        neighborhood = cast(Neighborhood, body.get("neighborhood") or {})
        prerequisites = neighborhood.get("prerequisites") or []
        dependents = neighborhood.get("dependents") or []
        if prerequisites:
            yield f"  prerequisites: {', '.join(_neighbor_label(n) for n in prerequisites)}"
        if dependents:
            yield f"  dependents: {', '.join(_neighbor_label(n) for n in dependents)}"
        yield f"  cost: {Cost.of(body.get('cost')).rendered}"
        escalation = body.get("escalation")
        if escalation:
            yield from _escalation_lines(escalation)


def _escalation_lines(escalation: dict[str, Any]) -> Iterator[str]:
    """The open escalation's block: its cause and detail, then the takeover command to paste,
    preferring the wrapped form."""
    yield f"  escalated: {escalation.get('cause') or 'unknown cause'}"
    if escalation.get("detail"):
        yield f"    detail: {escalation['detail']}"
    if escalation.get("wrapped_takeover_command"):
        yield f"    takeover: {escalation['wrapped_takeover_command']}"
    elif escalation.get("takeover_command"):
        yield f"    takeover: {escalation['takeover_command']}"


def _neighbor_label(neighbor: Neighbor) -> str:
    marker = "satisfied" if neighbor.get("satisfied") else "unsatisfied"
    return f"{neighbor['chunk_id']} ({marker})"


@dto
@dataclass(frozen=True)
class MigrationIntent:
    chunk_id: str
    body: dict[str, Any]
    cancelled: bool

    def lines(self) -> Iterator[str]:
        if self.cancelled:
            yield f"cleared {self.chunk_id}'s standing migration intent"
            return
        intent = self.body.get("intended_migration")
        if intent is None:
            # Shouldn't happen for a successful set, but degrade legibly rather than raise.
            yield f"{self.chunk_id}: migration intent not set"
            return
        target = intent.get("graph_name") or intent.get("graph_id")
        if intent.get("mode") == "forced":
            yield f"{self.chunk_id} will migrate to {target} node {intent.get('node_name')} at its next transition"
        else:
            yield f"{self.chunk_id} will auto-migrate to {target} at its next transition (name-matched node)"


class WorkItemListing(Listing):
    empty = "no work items"

    def line(self, row: Any) -> str:
        label = pointer_label(row)
        if row.get("error"):
            return f"{label}: error — {row['error']}"
        return f"{label}: {row.get('title') or '(no title)'}"


@click.group("chunk")
def chunk_group() -> None:
    """Create, inspect, edit, and move chunks."""


@chunk_group.command("list", cls=FleetCommand)
@project_option(required_by_hub=False)
def chunk_list(cli: CliContext, project: str | None) -> None:
    """List chunks with each one's status."""
    rows = cli.get_all("/api/chunks", "GET /chunks", key="chunks", params=project_query(project))
    cli.show(rows, ChunkListing(rows))


@chunk_group.command("show", cls=FleetCommand)
@click.argument("chunk_id")
def chunk_show(cli: CliContext, chunk_id: str) -> None:
    """Show one chunk: status, current node, holding runner, pointers, dependencies, and cost."""
    resp = cli.get(f"/api/chunks/{chunk_id}", "GET /chunks/{id}", on_status={404: f"unknown chunk {chunk_id}"})
    detail = resp.json()
    cli.show(detail, ChunkDetail(detail))


class Pointer(click.ParamType[str]):
    """One ``ingest`` argument as typed, bound to the token the hub is handed. The CLI carries no
    pointer grammar of its own, so a token travels through verbatim — bar a deprecated prefix."""

    name = "pointer"

    def convert(self, value: str, param: click.Parameter | None, ctx: click.Context | None) -> str:
        if not value.startswith("github:"):
            return value
        rest = value[len("github:") :]
        click.echo(
            f"warning: the 'github:' pointer prefix is deprecated (in {value!r}) — resolving {rest!r} on its own",
            err=True,
        )
        return rest


@chunk_group.command("ingest", cls=FleetCommand)
@click.argument("pointers", nargs=-1, required=True, type=Pointer())
@click.option(
    "--project",
    "project",
    default=None,
    metavar="PROJECT",
    help="The project to mint the chunk in, by id or slug. Without it, the project the pointers' work sources are "
    "linked to; needed when they name none or disagree.",
)
def chunk_ingest(cli: CliContext, pointers: tuple[str, ...], project: str | None) -> None:
    """Ingest work items by token, minting a chunk.

    Each POINTER is a source-native token — ``source:ref``, ``source#ref``, or a pasted
    work item URL; a batch mints one chunk carrying every pointer. Refused when no
    configured work source claims a token, a pointer's source is not linked to the
    project, or another chunk already holds a pointer."""
    tokens = list(pointers)
    request: dict[str, object] = {"tokens": tokens}
    if project is not None:
        request["project"] = project
    resp = cli.send("post", "/api/chunks", json_body=request)
    if resp.status_code == HTTPStatus.CONFLICT:
        conflict = resp.json()
        raise click.ClickException(
            f"pointer {pointer_label(conflict)} already held by chunk {conflict.get('existing_chunk_id')}"
        )
    cli.check(resp, "POST /chunks", on_status={422: "at least one token required"})
    body = resp.json()
    cli.show_lines(body, f"ingested {len(tokens)} pointer(s) → chunk {body['chunk_id']}")


@chunk_group.command("set", cls=FleetCommand)
@click.argument("chunk_id")
@click.option(
    "--graph",
    "graph_id",
    default=None,
    help="Repin CHUNK's workflow graph to this graph id. Legal only while CHUNK has never moved.",
)
@click.option(
    "--default-model",
    "default_model",
    multiple=True,
    help=(
        "Repin CHUNK's default model preference. Repeatable and ORDERED — the first entry "
        "that resolves at session mint wins. An entry is a `blizzard:` tier alias "
        "(frontier/advanced/basic) or a harness-native model name."
    ),
)
@click.option("--default-effort", "default_effort", default=None, help="Repin CHUNK's default effort.")
@click.option(
    "--default-harnesses",
    "default_harnesses",
    multiple=True,
    help=(
        "Repin CHUNK's default harness preference. Repeatable and ORDERED — the first entry "
        "that resolves at session mint wins."
    ),
)
def chunk_set(
    cli: CliContext,
    chunk_id: str,
    graph_id: str | None,
    default_model: tuple[str, ...],
    default_effort: str | None,
    default_harnesses: tuple[str, ...],
) -> None:
    """Repin CHUNK's graph and/or default model/effort/harnesses in one call.

    The given fields apply together or not at all; at least one option is required.
    Defaults are refused once a runner has claimed CHUNK, and ``--graph`` once CHUNK is
    claimed or has moved off its first node."""
    if graph_id is None and not default_model and default_effort is None and not default_harnesses:
        raise click.UsageError(
            "at least one of --graph/--default-model/--default-effort/--default-harnesses is required"
        )
    body: dict[str, object] = {}
    if graph_id is not None:
        body["graph_id"] = graph_id
    if default_model:
        body["default_model"] = list(default_model)
    if default_effort is not None:
        body["default_effort"] = default_effort
    if default_harnesses:
        body["default_harnesses"] = list(default_harnesses)
    resp = cli.patch(
        f"/api/chunks/{chunk_id}",
        "PATCH /chunks/{id}",
        json_body=body,
        on_status={404: f"unknown chunk {chunk_id}", 409: "chunk is not editable", 422: "invalid request"},
    )
    view = resp.json()
    parts: list[str] = []
    if graph_id is not None:
        parts.append(f"graph → {view['graph_id']}")
    if default_model:
        parts.append(f"default model → {', '.join(view.get('default_model') or []) or '-'}")
    if default_effort is not None:
        parts.append(f"default effort → {view.get('default_effort') or '-'}")
    if default_harnesses:
        parts.append(f"default harnesses → {', '.join(view.get('default_harnesses') or []) or '-'}")
    cli.show_lines(view, f"{chunk_id}: {', '.join(parts)}")


@chunk_group.command("promote", cls=FleetCommand)
@click.argument("chunk_id")
@click.option("--after", "after", default=None, help="Land right after this ready chunk; default is the queue tail.")
def chunk_promote(cli: CliContext, chunk_id: str, after: str | None) -> None:
    """Promote a not-ready CHUNK to ready so a runner may claim it.

    Promoting an already-ready chunk changes nothing. Refused when CHUNK is done or
    stopped, or ``--after`` names a chunk outside the ready queue or CHUNK itself."""
    resp = cli.post(
        f"/api/chunks/{chunk_id}/promote",
        "POST /chunks/{id}/promote",
        json_body={"after_chunk_id": after} if after is not None else None,
        on_status={
            409: "chunk is not promotable, or --after is not in the ready queue",
            404: f"no such chunk {chunk_id}",
            422: "a chunk cannot be placed after itself",
        },
    )
    cli.finish(resp, f"promoted {chunk_id} — now ready for a runner to claim")


@chunk_group.command("demote", cls=FleetCommand)
@click.argument("chunk_id")
@click.option(
    "--after", "after", default=None, help="Land right after this backlog chunk; default is the backlog tail."
)
def chunk_demote(cli: CliContext, chunk_id: str, after: str | None) -> None:
    """Demote a ready CHUNK back to not-ready, keeping its id, history, edges, pin, and defaults.

    Demoting a chunk that is not ready changes nothing. Refused when CHUNK is claimed,
    delivering, waiting on a human, or ended, or ``--after`` names a chunk outside the
    backlog or CHUNK itself. Detach a claimed chunk first."""
    resp = cli.post(
        f"/api/chunks/{chunk_id}/demote",
        "POST /chunks/{id}/demote",
        json_body={"after_chunk_id": after} if after is not None else None,
        on_status={
            409: "chunk is not demotable, or --after is not in the backlog",
            404: f"no such chunk {chunk_id}",
            422: "a chunk cannot be placed after itself",
        },
    )
    cli.finish(resp, f"demoted {chunk_id} — back to not ready")


@chunk_group.command("pause", cls=FleetCommand)
@click.argument("chunk_id")
@click.option("--by", "by", default="operator", help="Who is pausing, recorded as the actor.")
def chunk_pause(cli: CliContext, chunk_id: str, by: str) -> None:
    """Pause CHUNK — hold it where it stands until resumed.

    A runner holding the chunk parks its worker after a brief grace period, keeping the claim; an
    unclaimed chunk is held out of the queue. Refused when the chunk is done, stopped, or
    delivering."""
    resp = cli.post(
        f"/api/chunks/{chunk_id}/pause",
        "POST /chunks/{id}/pause",
        json_body={"by": by},
        on_status={409: "chunk is not pausable", 404: f"no such chunk {chunk_id}"},
    )
    cli.finish(
        resp,
        f"paused {chunk_id} — a runner holding it parks its worker after a brief grace period, "
        "keeping the claim; an unclaimed chunk stays out of the queue",
    )


@chunk_group.command("resume", cls=FleetCommand)
@click.argument("chunk_id")
@click.option("--by", "by", default="operator", help="Who is resuming, recorded as the actor.")
def chunk_resume(cli: CliContext, chunk_id: str, by: str) -> None:
    """Resume a paused CHUNK — clear the pause.

    A held chunk continues from its current node; an unclaimed one rejoins the queue.
    Resuming an unpaused chunk changes nothing; refused only when the chunk is unknown."""
    resp = cli.post(
        f"/api/chunks/{chunk_id}/resume",
        "POST /chunks/{id}/resume",
        json_body={"by": by},
        on_status={404: f"no such chunk {chunk_id}"},
    )
    cli.finish(
        resp,
        f"resumed {chunk_id} — the pause is cleared; a held chunk continues from its current node, "
        "an unclaimed one rejoins the queue",
    )


@chunk_group.command("detach", cls=FleetCommand)
@click.argument("chunk_id")
def chunk_detach(cli: CliContext, chunk_id: str) -> None:
    """Forcibly release CHUNK from its runner.

    The chunk is released from its runner and can be claimed again at its current node, unless an
    open escalation or question still holds it. Refused when nothing holds the chunk — a stopped or
    done chunk is held by nothing."""
    resp = cli.post(
        f"/api/chunks/{chunk_id}/detach",
        "POST /chunks/{id}/detach",
        on_status={409: "chunk has no live route holding a claim", 404: f"no such chunk {chunk_id}"},
    )
    cli.finish(
        resp,
        f"detached {chunk_id} — released from its runner, re-claimable at its current node "
        "unless an open escalation or question still holds it",
    )


@chunk_group.command("depend", cls=FleetCommand)
@click.argument("chunk_id")
@click.argument("prerequisite_chunk_id")
@click.option("--by", "by", default="operator", help="Who is declaring, recorded as the actor.")
def chunk_depend(cli: CliContext, chunk_id: str, prerequisite_chunk_id: str, by: str) -> None:
    """Declare that CHUNK_ID depends on PREREQUISITE_CHUNK_ID.

    CHUNK_ID waits until PREREQUISITE_CHUNK_ID is done. Declaring an existing
    dependency changes nothing. Refused when CHUNK_ID can no longer be edited, the dependency
    would close a cycle, or the prerequisite is ephemeral."""
    resp = cli.send(
        "post",
        f"/api/chunks/{chunk_id}/dependencies",
        json_body={"prerequisite_chunk_id": prerequisite_chunk_id, "by": by},
    )
    if resp.status_code == HTTPStatus.CONFLICT:
        body = resp.json()
        if "status" in body:
            raise click.ClickException(
                f"{body['chunk_id']} is not editable at status {body['status']} — cannot declare a dependency"
            )
        if "dependent_chunk_id" in body:
            raise click.ClickException(
                f"declaring {body['dependent_chunk_id']} → {body['prerequisite_chunk_id']} "
                "would close a cycle in the standing dependency graph"
            )
        raise click.ClickException(f"{body['chunk_id']} is ephemeral and cannot be named as a prerequisite")
    cli.check(
        resp,
        "POST /chunks/{id}/dependencies",
        on_status={404: f"unknown chunk {chunk_id} or {prerequisite_chunk_id}"},
    )
    cli.finish(resp, f"{chunk_id} now depends on {prerequisite_chunk_id}")


@chunk_group.command("release-dependency", cls=FleetCommand)
@click.argument("chunk_id")
@click.argument("prerequisite_chunk_id")
@click.option("--by", "by", default="operator", help="Who is releasing, recorded as the actor.")
def chunk_release_dependency(cli: CliContext, chunk_id: str, prerequisite_chunk_id: str, by: str) -> None:
    """Release CHUNK_ID's standing dependency on PREREQUISITE_CHUNK_ID.

    Refused when CHUNK_ID has no such dependency."""
    resp = cli.send(
        "post",
        f"/api/chunks/{chunk_id}/dependencies/release",
        json_body={"prerequisite_chunk_id": prerequisite_chunk_id, "by": by},
    )
    if resp.status_code == HTTPStatus.CONFLICT:
        body = resp.json()
        raise click.ClickException(
            f"no standing dependency from {body['dependent_chunk_id']} to {body['prerequisite_chunk_id']}"
        )
    cli.check(resp, "POST /chunks/{id}/dependencies/release", on_status={404: f"unknown chunk {chunk_id}"})
    cli.finish(resp, f"released {chunk_id}'s dependency on {prerequisite_chunk_id}")


@chunk_group.command("requeue", cls=FleetCommand)
@click.argument("chunk_id")
@click.option("--note", default=None, help="A note binding every later session on the chunk, as an answer does.")
def chunk_requeue(cli: CliContext, chunk_id: str, note: str | None) -> None:
    """Close CHUNK's open escalation and return it to the queue at its current node.

    Refused when CHUNK has no open escalation."""
    resp = cli.post(
        f"/api/chunks/{chunk_id}/requeues",
        "POST /chunks/{id}/requeues",
        json_body={"note": note} if note is not None else None,
        on_status={409: "chunk is not escalated", 404: f"no such chunk {chunk_id}"},
    )
    cli.finish(resp, f"requeued {chunk_id} — re-leasable at its current node")


@chunk_group.command("stop", cls=FleetCommand)
@click.argument("chunk_id")
@click.option("--by", "by", default="operator", help="Who is stopping, recorded as the actor.")
def chunk_stop(cli: CliContext, chunk_id: str, by: str) -> None:
    """Terminally abandon CHUNK — the operator's last-resort verb.

    CHUNK becomes ``stopped`` and is never offered to a runner again; its runner is
    released and any open escalation closed. Refused when CHUNK is already done or
    stopped. There is no ``un-stop``."""
    resp = cli.post(
        f"/api/chunks/{chunk_id}/stop",
        "POST /chunks/{id}/stop",
        json_body={"by": by},
        on_status={409: "chunk is not stoppable", 404: f"no such chunk {chunk_id}"},
    )
    cli.finish(resp, f"stopped {chunk_id} — terminally abandoned, its route (if any) released")


@chunk_group.command("done", cls=FleetCommand)
@click.argument("chunk_id")
@click.option("--by", "by", default="operator", help="Who is completing, recorded as the actor.")
def chunk_done(cli: CliContext, chunk_id: str, by: str) -> None:
    """Mark CHUNK done from any status, including ``stopped``.

    Its runner and any hub-exec slot are released, and its work items become eligible
    for closure. Completing a done chunk changes nothing."""
    resp = cli.post(
        f"/api/chunks/{chunk_id}/complete",
        "POST /chunks/{id}/complete",
        json_body={"by": by},
        on_status={404: f"no such chunk {chunk_id}"},
    )
    cli.finish(resp, f"completed {chunk_id} — done, its route (if any) released")


@chunk_group.command("restart", cls=FleetCommand)
@click.argument("chunk_id")
@click.option(
    "--to-graph",
    default=None,
    help="Move CHUNK onto this graph as part of the same move — a graph id, or a name resolved to the "
    "newest enabled mint of it. Omit to restart CHUNK where it stands; naming its own pin is refused.",
)
@click.option(
    "--node",
    default=None,
    help="Node name to force CHUNK onto, on --to-graph's graph when one is given and CHUNK's own "
    "otherwise. Omit for its current node's name, or that graph's entry node if CHUNK has never moved.",
)
@click.option("--by", "by", default="operator", help="Who is restarting, recorded as the actor.")
@click.option("--note", default=None, help="A note binding every later session on the chunk, as an answer does.")
def chunk_restart(
    cli: CliContext, chunk_id: str, to_graph: str | None, node: str | None, by: str, note: str | None
) -> None:
    """Force CHUNK onto a node now, on a freshly minted session.

    The move has happened when the command returns: the running worker is stopped and the chunk
    re-enters at the node, where ``migrate`` only takes effect at the next transition. Refused when
    CHUNK is done or stopped, the node is not on the graph, or ``--to-graph`` names CHUNK's own
    graph or a retired one."""
    resp = cli.post(
        f"/api/chunks/{chunk_id}/restart",
        "POST /chunks/{id}/restart",
        json_body={"node": node, "to_graph": to_graph, "by": by, **({"note": note} if note is not None else {})},
        on_status={409: "chunk is not restartable there", 404: f"no such chunk {chunk_id}"},
    )
    body = resp.json()
    landed = body.get("current_node_name") or node or "its current node"
    onto = f" on graph `{to_graph}`" if to_graph is not None else ""
    cli.show_lines(body, f"restarted {chunk_id} at `{landed}`{onto} — re-entering on a fresh session")


@chunk_group.command("migrate", cls=FleetCommand)
@click.argument("chunk_id")
@click.option("--to-graph", default=None, help="Migration target — a graph id or name. Required unless --cancel.")
@click.option(
    "--node",
    default=None,
    help="Force landing on this node name on the target graph (forced mode). Omit for auto (name-matched).",
)
@click.option("--cancel", is_flag=True, default=False, help="Clear the chunk's standing migration intent.")
def chunk_migrate(cli: CliContext, chunk_id: str, to_graph: str | None, node: str | None, cancel: bool) -> None:
    """Set, overwrite, or clear CHUNK's standing migration intent.

    ``--node`` present selects ``forced``, absent selects ``auto``; ``--cancel`` clears
    a standing intent and conflicts with ``--to-graph``/``--node``. The intent is consulted
    at the chunk's next transition."""
    if cancel and (to_graph is not None or node is not None):
        raise click.UsageError("--cancel cannot be combined with --to-graph/--node")
    if not cancel and to_graph is None:
        raise click.UsageError("--to-graph is required unless --cancel")

    if cancel:
        body: dict[str, object] = {"intended_migration": None}
    else:
        assert to_graph is not None, "checked above: --to-graph is required unless --cancel"
        intended: dict[str, str] = {"to_graph": to_graph}
        if node is not None:
            intended["node"] = node
        body = {"intended_migration": intended}

    resp = cli.patch(
        f"/api/chunks/{chunk_id}",
        "PATCH /chunks/{id}",
        json_body=body,
        on_status={
            404: f"unknown chunk {chunk_id}",
            409: "chunk is not editable",
            422: "invalid migration request",
        },
    )

    body = resp.json()
    cli.show(body, MigrationIntent(chunk_id, body, cancelled=cancel))


@chunk_group.command("group", cls=FleetCommand)
@click.argument("chunk_id")
@click.argument("merge_ids", nargs=-1, required=True)
def chunk_group_cmd(cli: CliContext, chunk_id: str, merge_ids: tuple[str, ...]) -> None:
    """Merge MERGE_IDS into CHUNK_ID, which keeps its own status and gains their work items
    and dependencies.

    Refused when any named chunk is claimed or finished, belongs to another project than
    CHUNK_ID, or the merged dependencies would close a cycle."""
    resp = cli.post(
        f"/api/chunks/{chunk_id}/group",
        "POST /chunks/{id}/group",
        json_body={"merge_chunk_ids": list(merge_ids)},
        on_status={404: f"unknown chunk {chunk_id}", 409: "one of the named chunks is not unacquired"},
    )
    body = resp.json()
    merged = ", ".join(body.get("merged_chunk_ids", [])) or "none"
    cli.show_lines(body, f"grouped into {body['chunk_id']} (merged: {merged})")


@chunk_group.command("delete", cls=FleetCommand)
@click.argument("chunk_id")
@click.option("--by", "by", default="operator", help="Who is deleting, recorded as the actor.")
@click.option("--yes", is_flag=True, default=False, help="Skip the confirmation prompt.")
def chunk_delete(cli: CliContext, chunk_id: str, by: str, yes: bool) -> None:
    """Delete CHUNK, withdrawing every open hub item it holds.

    Irreversible, so confirms first unless ``--yes``. Refused when CHUNK is claimed, done or
    stopped, or a prerequisite of another chunk, which the refusal names."""
    if not yes and not click.confirm(f"delete {chunk_id}? this withdraws its hub item(s) too"):
        raise click.Abort()
    resp = cli.delete(
        f"/api/chunks/{chunk_id}",
        "DELETE /chunks/{id}",
        json_body={"by": by},
        on_status={409: "chunk is not deletable", 404: f"no such chunk {chunk_id}"},
    )
    cli.finish(resp, f"deleted {chunk_id} — its hub item(s), if any, withdrawn")


@dataclass(frozen=True)
class WorkItems:
    """One chunk's work items, read and rendered, as a callable body shareable across verbs
    that cannot reach each other through ``Context.invoke``."""

    cli: CliContext
    chunk_id: str

    def show(self) -> None:
        resp = self.cli.get(
            f"/api/chunks/{self.chunk_id}/work-items",
            "GET /chunks/{id}/work-items",
            on_status={404: f"unknown chunk {self.chunk_id}"},
        )
        body = resp.json()
        self.cli.show(body, WorkItemListing(body.get("items", [])))


@chunk_group.command("work-items", cls=FleetCommand)
@click.argument("chunk_id")
def chunk_work_items(cli: CliContext, chunk_id: str) -> None:
    """Show CHUNK's work items as their sources report them, one entry per pointer.

    An item its source fails to fetch shows that error in its own entry; the rest still list."""
    WorkItems(cli, chunk_id).show()


@chunk_group.command("pm", hidden=True, cls=FleetCommand)
@click.argument("chunk_id")
def chunk_pm(cli: CliContext, chunk_id: str) -> None:
    """Deprecated alias for ``blizzard hub chunk work-items``."""
    click.echo(
        "warning: `blizzard hub chunk pm` is deprecated — use `blizzard hub chunk work-items`",
        err=True,
    )
    WorkItems(cli, chunk_id).show()
