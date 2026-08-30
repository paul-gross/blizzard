"""``blizzard hub graph`` — issues #101/#104/#123: operator verbs over minted graphs."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click
import httpx
import yaml

from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing
from blizzard.hub.graphs import GraphFile


class GraphListing(Listing):
    empty = "no graphs minted yet"

    def line(self, row: Any) -> str:
        marker = "effective" if row["effective"] else ("retired" if row["retired"] else "superseded")
        return f"{row['graph_id']}  name={row['name']}  {marker}  created_at={row['created_at']}"


class GraphSyncListing(Listing):
    empty = "no packaged graphs to reconcile"

    def line(self, row: Any) -> str:
        detail = f" — {row['detail']}" if row.get("detail") else ""
        graph_id = f" {row['graph_id']}" if row.get("graph_id") else ""
        return f"{row['name']}: {row['status']}{graph_id}{detail}"


@dataclass(frozen=True)
class GraphDetail:
    body: dict[str, Any]

    def lines(self) -> Iterator[str]:
        body = self.body
        marker = "retired" if body.get("retired") else "enabled"
        yield f"{body['graph_id']}  name={body['name']}  {marker}  entry={body.get('entry_node_id')}"
        for session in body.get("sessions", []):
            yield f"  session {session['name']}  {self._session_summary(session)}"
        for node in body.get("nodes", []):
            yield f"  node {node['node_id']}  name={node['name']}  executor={node.get('executor')}"
        for edge in body.get("edges", []):
            yield f"  edge {edge['from_node_id']} --[{edge.get('choice_id')}]--> {edge.get('to_node_name')}"

    @staticmethod
    def _session_summary(session: dict[str, Any]) -> str:
        parts = []
        if session.get("model"):
            parts.append(f"model={','.join(session['model'])}")
        if session.get("effort"):
            parts.append(f"effort={session['effort']}")
        if session.get("compaction_window"):
            parts.append(f"compaction_window={session['compaction_window']}")
        rotate = session.get("rotate") or {}
        bounds = ", ".join(f"{k}={v}" for k, v in rotate.items() if v is not None)
        if bounds:
            parts.append(f"rotate=({bounds})")
        return "  ".join(parts) if parts else "(no pinning)"


@click.group("graph")
def graph_group() -> None:
    """Operator verbs over minted graphs: list, inspect, mint, retire, re-enable."""


@graph_group.command("list", cls=FleetCommand)
def graph_list(cli: CliContext) -> None:
    """List every minted graph, newest first — name, graph_id, effective, retired."""
    rows = cli.get("/api/graphs", "GET /graphs").json()
    cli.show(rows, GraphListing(rows))


@graph_group.command("show", cls=FleetCommand)
@click.argument("graph_id")
def graph_show(cli: CliContext, graph_id: str) -> None:
    """One graph's full reified definition — session pools, nodes, and edges."""
    resp = cli.get(f"/api/graphs/{graph_id}", "GET /graphs/{id}", on_status={404: f"unknown graph {graph_id}"})
    body = resp.json()
    cli.show(body, GraphDetail(body))


@graph_group.command("mint", cls=FleetCommand)
@click.argument("path")
def graph_mint(cli: CliContext, path: str) -> None:
    """Mint a graph from PATH's YAML definition; PATH may be '-' to read stdin.

    A file PATH inlines file references relative to its own directory: a 'prompt'/'prompt_addendum' value only when it
    reads as a path, so literal prose stays literal, but every 'artifacts:' value always — one that fails to resolve
    fails the load, naming the entry. Stdin has no directory, so it posts verbatim; a 422 renders in full."""
    if path == "-":
        definition_yaml = click.get_text_stream("stdin").read()
    else:
        try:
            definition_yaml = GraphFile(Path(path)).inlined_yaml
        except (yaml.YAMLError, OSError, ValueError) as exc:
            raise click.ClickException(f"failed to load {path}: {exc}") from exc

    resp = cli.send("post", "/api/graphs", json_body={"definition_yaml": definition_yaml})
    if resp.status_code == httpx.codes.UNPROCESSABLE_ENTITY:
        report = resp.json()
        lines = [f"error: {e}" for e in report.get("errors", [])]
        lines += [f"warning: {w}" for w in report.get("warnings", [])]
        raise click.ClickException("graph definition invalid:\n" + "\n".join(lines))
    cli.check(resp, "POST /graphs")
    body = resp.json()
    warnings = [f"warning: {w}" for w in body.get("warnings", [])]
    cli.show_lines(body, f"minted graph {body['graph_id']}", *warnings)


@graph_group.command("sync", cls=FleetCommand)
def graph_sync(cli: CliContext) -> None:
    """Reconcile the hub's packaged graphs into its store, minting only what changed.

    The deploy verb (issue #146) — graphs live in the store, not on disk, so run it at
    the end of every deploy; it is idempotent. The **hub's own** packaged set is what is
    reconciled, not this CLI's. Exits non-zero only if a packaged graph failed to load."""
    resp = cli.post("/api/graphs/sync", "POST /graphs/sync", json_body={})
    body = resp.json()
    cli.show(body, GraphSyncListing(body.get("entries", [])))
    if not body.get("ok", True):
        raise click.ClickException("one or more packaged graphs failed to reconcile")


@graph_group.command("retire", cls=FleetCommand)
@click.argument("graph_id")
@click.option("--by", "by", default="operator", help="Who is retiring (recorded on the fact).")
def graph_retire(cli: CliContext, graph_id: str, by: str) -> None:
    """Retire GRAPH_ID — excludes it from name resolution; in-flight chunks run on."""
    _set_graph_lifecycle(cli, graph_id, verb="retire", by=by)


@graph_group.command("enable", cls=FleetCommand)
@click.argument("graph_id")
@click.option("--by", "by", default="operator", help="Who is re-enabling (recorded on the fact).")
def graph_enable(cli: CliContext, graph_id: str, by: str) -> None:
    """Re-enable a retired GRAPH_ID — restores normal newest-per-name derivation."""
    _set_graph_lifecycle(cli, graph_id, verb="enable", by=by)


@graph_group.command("follow-latest", cls=FleetCommand)
@click.argument("graph_id")
@click.argument("value", type=click.Choice(["true", "false", "inherit"]))
@click.option("--by", "by", default="operator", help="Who is setting the policy (recorded on the fact).")
def graph_follow_latest(cli: CliContext, graph_id: str, value: str, by: str) -> None:
    """Set GRAPH_ID's follow-latest policy: true, false, or inherit (issue #164).

    With the policy on, a chunk pinned to this mint re-pins to the newest enabled mint
    of the same *name* at its next transition. ``inherit`` (the stored ``null``, and
    every mint's default) defers to the hub's own ``follow_latest``."""
    follow_latest = None if value == "inherit" else value == "true"
    resp = cli.post(
        f"/api/graphs/{graph_id}/follow-latest",
        "POST /graphs/{id}/follow-latest",
        json_body={"follow_latest": follow_latest, "by": by},
        on_status={404: f"unknown graph {graph_id}"},
    )
    body = resp.json()
    stored = body.get("follow_latest")
    rendered = "inherit (the hub default)" if stored is None else str(stored).lower()
    cli.show_lines(body, f"graph {graph_id} follow-latest is now {rendered}")


def _set_graph_lifecycle(cli: CliContext, graph_id: str, *, verb: str, by: str) -> None:
    resp = cli.post(
        f"/api/graphs/{graph_id}/{verb}",
        f"POST /graphs/{{id}}/{verb}",
        json_body={"by": by},
        on_status={404: f"unknown graph {graph_id}"},
    )
    body = resp.json()
    state = "retired" if body.get("retired") else "enabled"
    cli.show_lines(body, f"graph {graph_id} is now {state}")
