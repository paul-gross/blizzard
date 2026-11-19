"""``blizzard hub graph`` — operator commands over minted graphs."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import click
from pydantic import TypeAdapter

from blizzard.cli.get_group import GetGroup
from blizzard.cli.refusal import refusal_of
from blizzard.foundation.roles import dto
from blizzard.hub.cli.command import AdminCommand, FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing
from blizzard.hub.documents import YAML_CODEC
from blizzard.hub.graphs import GraphFile
from blizzard.wire.graph import (
    GRAPH_DEFINITION_INVALID,
    GraphSessionView,
    GraphSummaryView,
    GraphSyncEntry,
    GraphView,
)
from blizzard.wire.tenant import AdminGraphSyncResponse, TenantGraphSync

_GRAPH_SUMMARIES = TypeAdapter(list[GraphSummaryView])
_GRAPH = TypeAdapter(GraphView)
_ADMIN_GRAPH_SYNC = TypeAdapter(AdminGraphSyncResponse)


class GraphListing(Listing):
    empty = "no graphs minted yet"

    def line(self, row: Any) -> str:
        graph: GraphSummaryView = row
        marker = "effective" if graph.effective else ("retired" if graph.retired else "superseded")
        return f"{graph.graph_id}  name={graph.name}  {marker}  created_at={graph.created_at}"


class GraphSyncListing(Listing):
    empty = "no packaged graphs to reconcile"

    def line(self, row: Any) -> str:
        entry: GraphSyncEntry = row
        detail = f" — {entry.detail}" if entry.detail else ""
        graph_id = f" {entry.graph_id}" if entry.graph_id else ""
        return f"{entry.name}: {entry.status}{graph_id}{detail}"


@dto
@dataclass(frozen=True)
class TenantGraphSyncReport:
    """One block per open tenant — its id and name, then a line per packaged graph."""

    tenants: list[TenantGraphSync]

    def lines(self) -> Iterator[str]:
        if not self.tenants:
            yield "no open tenants"
        for tenant in self.tenants:
            yield f"{tenant.tenant_id}  {tenant.name}"
            for line in GraphSyncListing(tenant.entries).lines():
                yield f"  {line}"


@dto
@dataclass(frozen=True)
class GraphDetail:
    body: GraphView

    def lines(self) -> Iterator[str]:
        body = self.body
        marker = "retired" if body.retired else "enabled"
        yield f"{body.graph_id}  name={body.name}  {marker}  entry={body.entry_node_id}"
        for session in body.sessions:
            yield f"  session {session.name}  {_session_summary(session)}"
        for node in body.nodes:
            yield f"  node {node.node_id}  name={node.name}  executor={node.executor}"
        for edge in body.edges:
            yield f"  edge {edge.from_node_id} --[{edge.choice_id}]--> {edge.to_node_name}"


def _session_summary(session: GraphSessionView) -> str:
    parts: list[str] = []
    if session.model:
        parts.append(f"model={','.join(session.model)}")
    if session.effort:
        parts.append(f"effort={session.effort}")
    if session.compaction_window:
        parts.append(f"compaction_window={session.compaction_window}")
    if session.harnesses:
        parts.append(f"harnesses={','.join(session.harnesses)}")
    rotate = session.rotate.model_dump() if session.rotate is not None else {}
    bounds = ", ".join(f"{k}={v}" for k, v in rotate.items() if v is not None)
    if bounds:
        parts.append(f"rotate=({bounds})")
    return "  ".join(parts) if parts else "(no pinning)"


@click.group("graph", cls=GetGroup)
def graph_group() -> None:
    """Mint and manage the workflow graphs chunks run on."""


@graph_group.command("list", cls=FleetCommand)
def graph_list(cli: CliContext) -> None:
    """List every minted graph, newest first — name, graph_id, effective, retired."""
    raw, rows = cli.decode(cli.get("/api/graphs", "GET /graphs"), _GRAPH_SUMMARIES, "GET /graphs")
    cli.show(raw, GraphListing(rows))


@graph_group.command("get", cls=FleetCommand)
@click.argument("graph_id")
def graph_show(cli: CliContext, graph_id: str) -> None:
    """One graph's full reified definition — session pools, nodes, and edges."""
    resp = cli.get(f"/api/graphs/{graph_id}", "GET /graphs/{id}")
    raw, body = cli.decode(resp, _GRAPH, "GET /graphs/{id}")
    cli.show(raw, GraphDetail(body))


@graph_group.command("mint", cls=FleetCommand)
@click.argument("path")
def graph_mint(cli: CliContext, path: str) -> None:
    """Mint a graph from PATH's definition; PATH may be '-' to read stdin.

    A file PATH is decoded by extension (.yaml, .yml, .json; else refused); YAML is strict — a duplicate key is an
    error, 'yes' or a date stays a string. It inlines file references relative to its own directory: a 'prompt' only
    when it reads as a path, every 'artifacts:' value always. Stdin is sent as is. Refusal lists every error."""
    if path == "-":
        definition_yaml = click.get_text_stream("stdin").read()
    else:
        try:
            definition_yaml = GraphFile(Path(path)).inlined_yaml(YAML_CODEC)
        except (OSError, ValueError) as exc:
            raise click.ClickException(f"failed to load {path}: {exc}") from exc

    resp = cli.send("post", "/api/graphs", json_body={"definition_yaml": definition_yaml})
    refusal = refusal_of(resp)
    if refusal.type == GRAPH_DEFINITION_INVALID:
        lines = [f"error: {e}" for e in cast(list[object], refusal.members.get("errors", []))]
        lines += [f"warning: {w}" for w in cast(list[object], refusal.members.get("warnings", []))]
        raise click.ClickException("graph definition invalid:\n" + "\n".join(lines))
    cli.check(resp, "POST /graphs")
    raw, body = cli.decode(resp, _GRAPH, "POST /graphs")
    warnings = [f"warning: {w}" for w in body.warnings]
    cli.show_lines(raw, f"minted graph {body.graph_id}", *warnings)


@graph_group.command("sync", cls=AdminCommand)
def graph_sync(cli: CliContext) -> None:
    """Reconcile the hub's packaged graphs into every open tenant, minting only what changed, and report per tenant.

    Idempotent. The **hub's own** packaged set is what is reconciled, not this CLI's.
    Exits non-zero only if a packaged graph failed to load in some tenant. When to run it:
    https://github.com/paul-gross/blizzard/blob/master/docs/deployment/install.md#graph-sync-after-a-deploy"""
    resp = cli.post("/api/admin/graphs/sync", "POST /admin/graphs/sync", json_body={})
    raw, body = cli.decode(resp, _ADMIN_GRAPH_SYNC, "POST /admin/graphs/sync")
    cli.show(raw, TenantGraphSyncReport(body.tenants))
    if not body.ok:
        raise click.ClickException("one or more packaged graphs failed to reconcile")


@graph_group.command("retire", cls=FleetCommand)
@click.argument("graph_id")
def graph_retire(cli: CliContext, graph_id: str) -> None:
    """Retire GRAPH_ID: its name no longer resolves to it; chunks already on it run on."""
    _set_graph_lifecycle(cli, graph_id, verb="retire")


@graph_group.command("enable", cls=FleetCommand)
@click.argument("graph_id")
def graph_enable(cli: CliContext, graph_id: str) -> None:
    """Re-enable a retired GRAPH_ID: its name resolves to it again when it is that name's newest mint."""
    _set_graph_lifecycle(cli, graph_id, verb="enable")


@graph_group.command("follow-latest", cls=FleetCommand)
@click.argument("graph_id")
@click.argument("value", type=click.Choice(["true", "false", "inherit"]))
def graph_follow_latest(cli: CliContext, graph_id: str, value: str) -> None:
    """Set GRAPH_ID's follow-latest policy: true, false, or inherit.

    With the policy on, a chunk on this mint moves to the newest enabled mint of the same
    *name* at its next transition. ``inherit``, every mint's default, follows the hub's
    own setting."""
    follow_latest = None if value == "inherit" else value == "true"
    resp = cli.patch(
        f"/api/graphs/{graph_id}",
        "PATCH /graphs/{id}",
        json_body={"follow_latest": follow_latest},
    )
    raw, body = cli.decode(resp, _GRAPH, "PATCH /graphs/{id}")
    stored = body.follow_latest
    rendered = "inherit (the hub default)" if stored is None else str(stored).lower()
    cli.show_lines(raw, f"graph {graph_id} follow-latest is now {rendered}")


def _set_graph_lifecycle(cli: CliContext, graph_id: str, *, verb: str) -> None:
    resp = cli.post(
        f"/api/graphs/{graph_id}/{verb}",
        f"POST /graphs/{{id}}/{verb}",
        json_body={},
    )
    raw, body = cli.decode(resp, _GRAPH, f"POST /graphs/{{id}}/{verb}")
    state = "retired" if body.retired else "enabled"
    cli.show_lines(raw, f"graph {graph_id} is now {state}")
