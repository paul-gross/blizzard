"""``blizzard hub routine`` — issue #389: operator verbs over routines."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import click
import httpx

from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing

_ROUTINE_MODEL_HELP = (
    "The routine's default model preference. Repeatable and ORDERED — the first entry "
    "that resolves at session mint wins."
)


class RoutineListing(Listing):
    empty = "no routines yet"

    def line(self, row: Any) -> str:
        return f"{row['routine_id']}  name={row['name']}  graph={row['graph_name']}  scope={row['default_scope_slug']}"


@dataclass(frozen=True)
class RoutineDetail:
    body: dict[str, Any]

    def lines(self) -> Iterator[str]:
        body = self.body
        yield f"{body['routine_id']}  name={body['name']}  graph={body['graph_name']}"
        yield f"  default scope: {body['default_scope_slug']}"
        models = ", ".join(body.get("default_model") or []) or "-"
        yield f"  default model: {models}   default effort: {body.get('default_effort') or '-'}"


@click.group("routine")
def routine_group() -> None:
    """Operator verbs over routines: create, list, inspect, edit."""


@routine_group.command("create", cls=FleetCommand)
@click.argument("name")
@click.argument("graph_name")
@click.argument("default_scope_slug")
@click.option("--model", "default_model", multiple=True, help=_ROUTINE_MODEL_HELP)
@click.option("--effort", "default_effort", default=None, help="The routine's default effort.")
def routine_create(
    cli: CliContext,
    name: str,
    graph_name: str,
    default_scope_slug: str,
    default_model: tuple[str, ...],
    default_effort: str | None,
) -> None:
    """Mint a routine named NAME, running GRAPH_NAME with DEFAULT_SCOPE_SLUG's scope.

    DEFAULT_SCOPE_SLUG mints a fresh scope if unseen (D4). GRAPH_NAME must resolve to
    an enabled graph."""
    resp = cli.send(
        "post",
        "/api/routines",
        json_body={
            "name": name,
            "graph_name": graph_name,
            "default_scope_slug": default_scope_slug,
            "default_model": list(default_model),
            "default_effort": default_effort,
        },
    )
    if resp.status_code == httpx.codes.UNPROCESSABLE_ENTITY:
        raise click.ClickException(f"routine rejected: {cli.detail(resp, 'validation failed')}")
    cli.check(resp, "POST /routines")
    body = resp.json()
    cli.show_lines(body, f"minted routine {body['routine_id']}")


@routine_group.command("list", cls=FleetCommand)
def routine_list(cli: CliContext) -> None:
    """List every routine, newest first — routine_id, name, graph, default scope."""
    rows = cli.get("/api/routines", "GET /routines").json()
    cli.show(rows, RoutineListing(rows))


@routine_group.command("show", cls=FleetCommand)
@click.argument("routine_id")
def routine_show(cli: CliContext, routine_id: str) -> None:
    """One routine's whole record — name, graph, default scope, model/effort defaults."""
    resp = cli.get(
        f"/api/routines/{routine_id}", "GET /routines/{id}", on_status={404: f"unknown routine {routine_id}"}
    )
    body = resp.json()
    cli.show(body, RoutineDetail(body))


@routine_group.command("edit", cls=FleetCommand)
@click.argument("routine_id")
@click.option("--graph", "graph_name", required=True, help="The routine's graph name.")
@click.option("--scope", "default_scope_slug", required=True, help="The routine's default scope slug.")
@click.option("--model", "default_model", multiple=True, help=_ROUTINE_MODEL_HELP)
@click.option("--effort", "default_effort", default=None, help="The routine's default effort.")
def routine_edit(
    cli: CliContext,
    routine_id: str,
    graph_name: str,
    default_scope_slug: str,
    default_model: tuple[str, ...],
    default_effort: str | None,
) -> None:
    """Change ROUTINE_ID's graph, default scope, and model/effort defaults; its name
    never changes here."""
    resp = cli.get(
        f"/api/routines/{routine_id}", "GET /routines/{id}", on_status={404: f"unknown routine {routine_id}"}
    )
    name = resp.json()["name"]
    resp = cli.send(
        "patch",
        f"/api/routines/{routine_id}",
        json_body={
            "name": name,
            "graph_name": graph_name,
            "default_scope_slug": default_scope_slug,
            "default_model": list(default_model),
            "default_effort": default_effort,
        },
    )
    if resp.status_code == httpx.codes.UNPROCESSABLE_ENTITY:
        raise click.ClickException(f"routine edit rejected: {cli.detail(resp, 'validation failed')}")
    cli.check(resp, "PATCH /routines/{id}", on_status={404: f"unknown routine {routine_id}"})
    body = resp.json()
    cli.show_lines(body, f"routine {routine_id} updated")
