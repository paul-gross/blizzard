"""``blizzard hub chunk`` — issue #104: operator verbs over one chunk (ingest, inspect, edit, transition)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import click
import httpx

from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import ChunkRow, Cost, Listing


class ChunkListing(Listing):
    empty = "no chunks"

    def line(self, row: Any) -> str:
        return ChunkRow(row).line()


@dataclass(frozen=True)
class ChunkDetail:
    body: dict[str, Any]

    def lines(self):
        body = self.body
        yield f"{body['chunk_id']}  status={body['status']}  graph={body.get('graph_name') or body['graph_id']}"
        yield f"  node: {ChunkRow(body).node}"
        # Both defaults on their own line: `chunk set` can write either, so a text-mode
        # read-back exists for both. `-` is "express no preference", not unknown.
        models = ", ".join(body.get("default_model") or []) or "-"
        yield f"  default model: {models}   default effort: {body.get('default_effort') or '-'}"
        pointers = body.get("work_refs") or []
        if pointers:
            labels = ", ".join(p.get("label") or f"{p['source']}#{p['ref']}" for p in pointers)
            yield f"  pointers: {labels}"
        route = body.get("route")
        if route:
            yield f"  runner: {route['runner_id']}  environments: {len(route.get('environment_ids', []))}"
        yield f"  cost: {Cost.of(body.get('cost')).rendered}"


@dataclass(frozen=True)
class MigrationIntent:
    chunk_id: str
    body: dict[str, Any]
    cancelled: bool

    def lines(self):
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
        label = row.get("label") or f"{row['source']}#{row['ref']}"
        if row.get("error"):
            return f"{label}: error — {row['error']}"
        return f"{label}: {row.get('title') or '(no title)'}"


@click.group("chunk")
def chunk_group() -> None:
    """Operator verbs over one chunk: ingest, inspect, edit, and transition it."""


@chunk_group.command("list", cls=FleetCommand)
def chunk_list(cli: CliContext) -> None:
    """The fleet chunk list — derived status per chunk."""
    rows = cli.get("/api/chunks", "GET /chunks").json()
    cli.show(rows, ChunkListing(rows))


@chunk_group.command("show", cls=FleetCommand)
@click.argument("chunk_id")
def chunk_show(cli: CliContext, chunk_id: str) -> None:
    """One chunk's full aggregate — status, current node, route, pointers, cost."""
    resp = cli.get(f"/api/chunks/{chunk_id}", "GET /chunks/{id}", on_status={404: f"unknown chunk {chunk_id}"})
    detail = resp.json()
    cli.show(detail, ChunkDetail(detail))


class Pointer(click.ParamType):
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
def chunk_ingest(cli: CliContext, pointers: tuple[str, ...]) -> None:
    """Ingest work items by token, minting a chunk.

    Each POINTER is a source-native token — ``source:ref``, ``source#ref``, or a pasted
    work item URL; a batch mints one chunk carrying every pointer. 422 when no
    configured work source claims a token; 409 when a pointer is already held."""
    tokens = list(pointers)
    resp = cli.send("post", "/api/chunks", json_body={"tokens": tokens})
    if resp.status_code == httpx.codes.CONFLICT:
        conflict = resp.json()
        raise click.ClickException(
            f"pointer {conflict.get('source')}#{conflict.get('ref')} already held by "
            f"chunk {conflict.get('existing_chunk_id')}"
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
def chunk_set(
    cli: CliContext, chunk_id: str, graph_id: str | None, default_model: tuple[str, ...], default_effort: str | None
) -> None:
    """Repin CHUNK's graph and/or default model/effort in one call (issues #104, #144).

    A pure client of ``PATCH /api/chunks/{id}``, naming whichever fields were given and
    applied all-or-nothing. At least one option is required; 409 for the defaults once
    CHUNK is claimed, and for ``--graph`` once it is claimed or has moved (#271)."""
    if graph_id is None and not default_model and default_effort is None:
        raise click.UsageError("at least one of --graph/--default-model/--default-effort is required")
    body: dict[str, object] = {}
    if graph_id is not None:
        body["graph_id"] = graph_id
    if default_model:
        body["default_model"] = list(default_model)
    if default_effort is not None:
        body["default_effort"] = default_effort
    resp = cli.patch(
        f"/api/chunks/{chunk_id}",
        "PATCH /chunks/{id}",
        json_body=body,
        on_status={404: f"unknown chunk {chunk_id}", 409: "chunk is not editable", 422: "invalid request"},
    )
    view = resp.json()
    parts = []
    if graph_id is not None:
        parts.append(f"graph → {view['graph_id']}")
    if default_model:
        parts.append(f"default model → {', '.join(view.get('default_model') or []) or '-'}")
    if default_effort is not None:
        parts.append(f"default effort → {view.get('default_effort') or '-'}")
    cli.show_lines(view, f"{chunk_id}: {', '.join(parts)}")


@chunk_group.command("promote", cls=FleetCommand)
@click.argument("chunk_id")
def chunk_promote(cli: CliContext, chunk_id: str) -> None:
    """Promote a not-ready CHUNK to ready so a runner may claim it.

    A pure client of the hub API: ``POST /api/chunks/{id}/promote``. Idempotent — promoting
    an already-ready chunk is a harmless no-op; 404 only when the chunk is unknown."""
    resp = cli.post(
        f"/api/chunks/{chunk_id}/promote", "POST /chunks/{id}/promote", on_status={404: f"no such chunk {chunk_id}"}
    )
    cli.finish(resp, f"promoted {chunk_id} — now ready for a runner to claim")


@chunk_group.command("pause", cls=FleetCommand)
@click.argument("chunk_id")
@click.option("--by", "by", default="operator", help="Who is pausing (recorded on the fact).")
def chunk_pause(cli: CliContext, chunk_id: str, by: str) -> None:
    """Pause CHUNK — the runner kills and parks the worker but keeps the claim (issue #46).

    A pure client of the hub API: ``POST /api/chunks/{id}/pause``. Unlike ``detach``, no
    route is released and no retry is consumed. 409 when the chunk is done/stopped/
    delivering."""
    resp = cli.post(
        f"/api/chunks/{chunk_id}/pause",
        "POST /chunks/{id}/pause",
        json_body={"by": by},
        on_status={409: "chunk is not pausable", 404: f"no such chunk {chunk_id}"},
    )
    cli.finish(resp, f"paused {chunk_id} — its worker will be killed and parked, keeping the claim")


@chunk_group.command("resume", cls=FleetCommand)
@click.argument("chunk_id")
@click.option("--by", "by", default="operator", help="Who is resuming (recorded on the fact).")
def chunk_resume(cli: CliContext, chunk_id: str, by: str) -> None:
    """Resume a paused CHUNK — the runner resumes the parked worker in place (issue #46).

    A pure client of the hub API: ``POST /api/chunks/{id}/resume``. Idempotent: resuming
    an unpaused chunk is a harmless no-op. 404 only when the chunk is unknown."""
    resp = cli.post(
        f"/api/chunks/{chunk_id}/resume",
        "POST /chunks/{id}/resume",
        json_body={"by": by},
        on_status={404: f"no such chunk {chunk_id}"},
    )
    cli.finish(resp, f"resumed {chunk_id} — its worker resumes in place")


@chunk_group.command("detach", cls=FleetCommand)
@click.argument("chunk_id")
def chunk_detach(cli: CliContext, chunk_id: str) -> None:
    """Forcibly release CHUNK from its runner.

    A pure client of the hub API: ``POST /api/chunks/{id}/detach``. The chunk re-derives
    ready and is re-claimable at its current node; the holding runner releases it on its
    next tick. 409 when the chunk has no live route to release."""
    resp = cli.post(
        f"/api/chunks/{chunk_id}/detach",
        "POST /chunks/{id}/detach",
        on_status={409: "chunk has no live route", 404: f"no such chunk {chunk_id}"},
    )
    cli.finish(resp, f"detached {chunk_id} — released from its runner, re-claimable at its current node")


@chunk_group.command("requeue", cls=FleetCommand)
@click.argument("chunk_id")
def chunk_requeue(cli: CliContext, chunk_id: str) -> None:
    """Close an escalation by supersession: requeue CHUNK at its current node."""
    resp = cli.post(
        f"/api/chunks/{chunk_id}/requeues",
        "POST /chunks/{id}/requeues",
        on_status={409: "chunk is not escalated", 404: f"no such chunk {chunk_id}"},
    )
    cli.finish(resp, f"requeued {chunk_id} — re-leasable at its current node")


@chunk_group.command("stop", cls=FleetCommand)
@click.argument("chunk_id")
@click.option("--by", "by", default="operator", help="Who is stopping (recorded on the fact).")
def chunk_stop(cli: CliContext, chunk_id: str, by: str) -> None:
    """Terminally abandon CHUNK — the operator's last-resort verb (issue #118).

    A pure client of ``POST /api/chunks/{id}/stop``. The chunk derives ``stopped`` and
    never re-derives ``ready``; any live route is released and any open escalation closed
    in the same operation. 409 when already done/stopped. There is no ``un-stop``."""
    resp = cli.post(
        f"/api/chunks/{chunk_id}/stop",
        "POST /chunks/{id}/stop",
        json_body={"by": by},
        on_status={409: "chunk is not stoppable", 404: f"no such chunk {chunk_id}"},
    )
    cli.finish(resp, f"stopped {chunk_id} — terminally abandoned, its route (if any) released")


@chunk_group.command("done", cls=FleetCommand)
@click.argument("chunk_id")
@click.option("--by", "by", default="operator", help="Who is completing (recorded on the fact).")
def chunk_done(cli: CliContext, chunk_id: str, by: str) -> None:
    """Manually complete CHUNK, from any non-``done`` status, including ``stopped`` (issue #294).
    A pure client of ``POST /api/chunks/{id}/complete``. The chunk derives ``done``; any live
    route and held hub-exec slot are released in the same operation, and its work refs become
    eligible for closure. Idempotent — an already-``done`` chunk is a harmless no-op, never
    refused."""
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
@click.option("--by", "by", default="operator", help="Who is restarting (recorded on the fact).")
def chunk_restart(cli: CliContext, chunk_id: str, to_graph: str | None, node: str | None, by: str) -> None:
    """Force CHUNK onto a node now, on a freshly minted session (issues #370, #371).

    A pure client of ``POST /api/chunks/{id}/restart``. The move has already happened when the call
    returns: the bumped epoch tears the running attempt down and re-enters, where ``migrate`` only
    records an intent for the next transition. 409 when CHUNK is terminal or the target refuses it."""
    resp = cli.post(
        f"/api/chunks/{chunk_id}/restart",
        "POST /chunks/{id}/restart",
        json_body={"node": node, "to_graph": to_graph, "by": by},
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
    """Set, overwrite, or clear CHUNK's standing migration intent (issue #124).

    ``--node`` present selects ``forced``, absent selects ``auto``; ``--cancel`` clears
    a standing intent and conflicts with ``--to-graph``/``--node``. The intent is consulted at
    the chunk's next transition, never applied eagerly — ``restart --to-graph`` moves it now."""
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
    """Merge MERGE_IDS into CHUNK_ID, the survivor — a pure client of
    ``POST /api/chunks/{id}/group``. Every id must be **unacquired** (409 otherwise); the
    survivor absorbs the union of work refs, keeps its own status, and absorbs each
    merged chunk's standing dependency edges, refused 409 if that would close a cycle."""
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
@click.option("--by", "by", default="operator", help="Who is deleting (recorded on the fact).")
@click.option("--yes", is_flag=True, default=False, help="Skip the confirmation prompt.")
def chunk_delete(cli: CliContext, chunk_id: str, by: str, yes: bool) -> None:
    """Delete unacquired CHUNK, withdrawing every open hub item it holds (issue #364) — a
    pure client of ``DELETE /api/chunks/{id}``. Irreversible, so confirms first unless
    ``--yes``. 409 when CHUNK is held, terminal, or a standing prerequisite for another
    chunk, which the response names; 404 only when CHUNK is unknown."""
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
    """One chunk's work items, read and rendered — the body ``work-items`` and its deprecated
    ``pm`` alias share, since a ``cls=``-built verb cannot be reached through ``Context.invoke``."""

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
    """CHUNK's work items, pass-through — one entry per work ref, vendor-native.

    A pure client of ``GET /api/chunks/{id}/work-items``; a per-pointer forge failure
    degrades to that entry's own ``error`` rather than failing the whole read."""
    WorkItems(cli, chunk_id).show()


@chunk_group.command("pm", hidden=True, cls=FleetCommand)
@click.argument("chunk_id")
def chunk_pm(cli: CliContext, chunk_id: str) -> None:
    """Deprecated alias for ``blizzard hub chunk work-items`` (issue #55)."""
    click.echo(
        "warning: `blizzard hub chunk pm` is deprecated — use `blizzard hub chunk work-items`",
        err=True,
    )
    WorkItems(cli, chunk_id).show()
