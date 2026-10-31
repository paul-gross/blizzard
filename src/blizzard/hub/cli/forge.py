"""Operator verbs over forge records."""

from __future__ import annotations

from typing import Any

import click

from blizzard.cli.get_group import GetGroup
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing

_IF_MATCH = click.option(
    "--if-match", type=int, default=None, help="Refuse the change unless the forge is still at this revision."
)


def _state(row: Any) -> str:
    return "retired" if row["retired"] else "enabled"


class ForgeListing(Listing):
    empty = "no forges yet"

    def line(self, row: Any) -> str:
        return f"{row['slug']}  {row['name']}  {row['forge_id']}  r{row['revision']}  {_state(row)}  {row['api_base']}"


def _refusals(forge: str) -> dict[int, str]:
    return {404: f"unknown forge {forge}", 409: f"forge {forge} cannot be changed", 422: "invalid forge"}


@click.group("forge", cls=GetGroup)
def forge_group() -> None:
    """Create, list, get, edit, retire and enable forges."""


@forge_group.command("create", cls=FleetCommand)
@click.argument("slug")
@click.option("--name", default=None, help="The forge's display name (default: SLUG).")
@click.option("--kind", required=True, help="The forge kind (github).")
@click.option("--api-base", required=True, help="The forge's HTTP API origin.")
@click.option("--web-base", default=None, help="The forge's web origin (default: derived from its API origin).")
def forge_create(cli: CliContext, slug: str, name: str | None, kind: str, api_base: str, web_base: str | None) -> None:
    """Add forge SLUG at revision 1."""
    body = {"slug": slug, "name": name, "kind": kind, "api_base": api_base, "web_base": web_base}
    row = cli.post("/api/forges", "POST /forges", json_body=body, on_status=_refusals(slug), door=True).json()
    cli.show_lines(row, f"forge {row['slug']} created at revision 1 — id {row['forge_id']}")


@forge_group.command("list", cls=FleetCommand)
@click.option("--include-retired", is_flag=True, help="Also list retired forges.")
def forge_list(cli: CliContext, include_retired: bool) -> None:
    """List forges by slug with their id, revision, state and API origin."""
    params = {"include_retired": "true"} if include_retired else None
    rows = cli.get("/api/forges", "GET /forges", params=params).json()["forges"]
    cli.show(rows, ForgeListing(rows))


@forge_group.command("get", cls=FleetCommand)
@click.argument("forge")
def forge_show(cli: CliContext, forge: str) -> None:
    """Show FORGE by id, current slug or former slug, with the capabilities it offers, its linked records and each rate
    budget's last reading."""
    row = cli.get(f"/api/forges/{forge}", "GET /forges/{forge}", on_status=_refusals(forge)).json()
    cli.show_lines(
        row,
        f"{row['slug']}  {row['name']}  revision {row['revision']}  {_state(row)}",
        f"id {row['forge_id']}  kind {row['kind']}",
        f"api_base {row['api_base']}  web_base {row['web_base']}  web_origin {row['web_origin']}",
        f"created {row['created_at']} by {row['created_by']}",
        f"offers {', '.join(row.get('offered', [])) or '-'}",
        *(f"repository {r['label']}  {r['key']}" for r in row["repositories"]),
        *(f"work source {s['label']}  {s['key']}" for s in row["work_sources"]),
        *(
            f"budget {b['secret_name'] or '(anonymous)'}  {b['bucket']}  {b['remaining']}/{b['limit']}  "
            f"resets {b['reset']}"
            for b in row["budgets"]
        ),
    )


@forge_group.command("edit", cls=FleetCommand)
@click.argument("forge")
@click.option("--slug", default=None, help="The new slug; the former one keeps resolving until claimed.")
@click.option("--name", default=None, help="The new display name.")
@click.option("--api-base", default=None, help="The new API origin.")
@click.option("--web-base", default=None, help="The new web origin.")
@click.option("--clear-web-base", is_flag=True, help="Derive the web origin from the API origin.")
@_IF_MATCH
def forge_edit(
    cli: CliContext,
    forge: str,
    slug: str | None,
    name: str | None,
    api_base: str | None,
    web_base: str | None,
    clear_web_base: bool,
    if_match: int | None,
) -> None:
    """Change only the stated fields of FORGE; a no-op keeps its revision."""
    if web_base is not None and clear_web_base:
        raise click.UsageError("choose either --web-base or --clear-web-base")
    body: dict[str, str | None] = {
        k: v
        for k, v in {"slug": slug, "name": name, "api_base": api_base, "web_base": web_base}.items()
        if v is not None
    }
    if clear_web_base:
        body["web_base"] = None
    if not body:
        raise click.UsageError("nothing to change — give a field to set")
    row = cli.patch(
        f"/api/forges/{forge}",
        "PATCH /forges/{forge}",
        json_body=body,
        on_status=_refusals(forge),
        if_match=if_match,
        door=True,
    ).json()
    cli.show_lines(row, f"forge {row['slug']} is at revision {row['revision']}")


@forge_group.command("retire", cls=FleetCommand)
@click.argument("forge")
@_IF_MATCH
def forge_retire(cli: CliContext, forge: str, if_match: int | None) -> None:
    """Retire FORGE, refusing while an enabled record names it."""
    _lifecycle(cli, forge, "retire", if_match)


@forge_group.command("enable", cls=FleetCommand)
@click.argument("forge")
@_IF_MATCH
def forge_enable(cli: CliContext, forge: str, if_match: int | None) -> None:
    """Re-enable FORGE."""
    _lifecycle(cli, forge, "enable", if_match)


def _lifecycle(cli: CliContext, forge: str, verb: str, if_match: int | None) -> None:
    row = cli.post(
        f"/api/forges/{forge}/{verb}",
        f"POST /forges/{{forge}}/{verb}",
        on_status=_refusals(forge),
        if_match=if_match,
        door=True,
    ).json()
    cli.show_lines(row, f"forge {row['slug']} is now {_state(row)} at revision {row['revision']}")
