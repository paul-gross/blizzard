"""``blizzard hub scope`` — issue #389: operator verbs over scopes."""

from __future__ import annotations

from typing import Any

import click

from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing


class ScopeListing(Listing):
    empty = "no scopes yet"

    def line(self, row: Any) -> str:
        marker = "retired" if row["retired"] else "enabled"
        description = f"  {row['description']}" if row.get("description") else ""
        return f"{row['slug']}  {marker}{description}"


@click.group("scope")
def scope_group() -> None:
    """Operator verbs over scopes: create, list, edit, retire, re-enable."""


@scope_group.command("create", cls=FleetCommand)
@click.argument("slug")
@click.option("--description", default="", help="The scope's description.")
def scope_create(cli: CliContext, slug: str, description: str) -> None:
    """Mint SLUG, or no-op onto the existing scope of the same slug.

    A re-create never overwrites a stored description — ``scope edit`` is the verb that
    changes it."""
    resp = cli.post("/api/scopes", "POST /scopes", json_body={"slug": slug, "description": description})
    body = resp.json()
    cli.show_lines(body, f"scope {body['slug']} ready")


@scope_group.command("list", cls=FleetCommand)
def scope_list(cli: CliContext) -> None:
    """List every scope, newest first — slug, retired, description."""
    rows = cli.get("/api/scopes", "GET /scopes").json()
    cli.show(rows, ScopeListing(rows))


@scope_group.command("edit", cls=FleetCommand)
@click.argument("slug")
@click.option("--description", required=True, help="The scope's new description.")
def scope_edit(cli: CliContext, slug: str, description: str) -> None:
    """Change SLUG's stored description in place; never touches its slug."""
    resp = cli.patch(
        f"/api/scopes/{slug}",
        "PATCH /scopes/{slug}",
        json_body={"description": description},
        on_status={404: f"unknown scope {slug}"},
    )
    body = resp.json()
    cli.show_lines(body, f"scope {slug} updated")


@scope_group.command("retire", cls=FleetCommand)
@click.argument("slug")
@click.option("--by", "by", default="operator", help="Who is retiring (recorded on the fact).")
def scope_retire(cli: CliContext, slug: str, by: str) -> None:
    """Retire SLUG — a reversible brake; in-flight users of it are untouched."""
    _set_scope_lifecycle(cli, slug, verb="retire", by=by)


@scope_group.command("enable", cls=FleetCommand)
@click.argument("slug")
@click.option("--by", "by", default="operator", help="Who is re-enabling (recorded on the fact).")
def scope_enable(cli: CliContext, slug: str, by: str) -> None:
    """Re-enable a retired SLUG."""
    _set_scope_lifecycle(cli, slug, verb="enable", by=by)


def _set_scope_lifecycle(cli: CliContext, slug: str, *, verb: str, by: str) -> None:
    resp = cli.post(
        f"/api/scopes/{slug}/{verb}",
        f"POST /scopes/{{slug}}/{verb}",
        json_body={"by": by},
        on_status={404: f"unknown scope {slug}"},
    )
    body = resp.json()
    state = "retired" if body.get("retired") else "enabled"
    cli.show_lines(body, f"scope {slug} is now {state}")
