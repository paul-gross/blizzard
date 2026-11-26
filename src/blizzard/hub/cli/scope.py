"""``blizzard hub scope`` — operator commands over scopes."""

from __future__ import annotations

import click
from pydantic import TypeAdapter

from blizzard.cli.get_group import GetGroup
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.project_option import project_option, project_path, project_query
from blizzard.hub.cli.views import Listing
from blizzard.wire.scope import ScopeView

_SCOPE = TypeAdapter(ScopeView)
_SCOPES = TypeAdapter(list[ScopeView])


class ScopeListing(Listing):
    empty = "no scopes yet"

    def line(self, row: ScopeView) -> str:
        marker = "retired" if row.retired else "enabled"
        description = f"  {row.description}" if row.description else ""
        return f"{row.slug}  {marker}{description}"


@click.group("scope", cls=GetGroup)
def scope_group() -> None:
    """Manage scopes, the named buckets findings are grouped into."""


@scope_group.command("create", cls=FleetCommand)
@click.argument("slug")
@click.option("--description", default="", help="The scope's description.")
@project_option(required_by_hub=True)
def scope_create(cli: CliContext, slug: str, description: str, project: str | None) -> None:
    """Mint SLUG, or no-op onto the existing scope of the same slug.

    A re-create never overwrites a stored description — ``scope edit`` is the verb that
    changes it."""
    resp = cli.post(
        project_path(project, "/scopes"),
        "POST /scopes",
        json_body={"slug": slug, "description": description},
        door=True,
    )
    raw, body = cli.decode(resp, _SCOPE, "POST /scopes")
    cli.show_lines(raw, f"scope {body.slug} ready")


@scope_group.command("list", cls=FleetCommand)
@project_option(required_by_hub=False)
def scope_list(cli: CliContext, project: str | None) -> None:
    """List every scope, newest first — slug, retired, description."""
    resp = cli.get("/api/scopes", "GET /scopes", params=project_query(project) or None)
    raw, rows = cli.decode(resp, _SCOPES, "GET /scopes")
    cli.show(raw, ScopeListing(rows))


@scope_group.command("get", cls=FleetCommand)
@click.argument("slug")
@project_option(required_by_hub=True)
def scope_show(cli: CliContext, slug: str, project: str | None) -> None:
    """Show SLUG's record — state, description, revision, and when it was created."""
    resp = cli.get(project_path(project, f"/scopes/{slug}"), "GET /scopes/{slug}")
    raw, body = cli.decode(resp, _SCOPE, "GET /scopes/{slug}")
    state = "retired" if body.retired else "enabled"
    cli.show_lines(
        raw,
        f"{body.slug}  {state}",
        f"description {body.description or '-'}",
        f"revision {body.revision if body.revision is not None else '-'}",
        f"created {body.created_at}",
    )


@scope_group.command("edit", cls=FleetCommand)
@click.argument("slug")
@click.option("--description", required=True, help="The scope's new description.")
@project_option(required_by_hub=True)
def scope_edit(cli: CliContext, slug: str, description: str, project: str | None) -> None:
    """Change SLUG's stored description in place; never touches its slug."""
    resp = cli.patch(
        project_path(project, f"/scopes/{slug}"),
        "PATCH /scopes/{slug}",
        json_body={"description": description},
        door=True,
    )
    raw, _ = cli.decode(resp, _SCOPE, "PATCH /scopes/{slug}")
    cli.show_lines(raw, f"scope {slug} updated")


@scope_group.command("retire", cls=FleetCommand)
@click.argument("slug")
@click.option("--by", expose_value=False, deprecated=True, help="Ignored: the hub records the signed-in caller.")
@project_option(required_by_hub=True)
def scope_retire(cli: CliContext, slug: str, project: str | None) -> None:
    """Retire SLUG — a reversible brake; in-flight users of it are untouched."""
    _set_scope_lifecycle(cli, slug, verb="retire", project=project)


@scope_group.command("enable", cls=FleetCommand)
@click.argument("slug")
@click.option("--by", expose_value=False, deprecated=True, help="Ignored: the hub records the signed-in caller.")
@project_option(required_by_hub=True)
def scope_enable(cli: CliContext, slug: str, project: str | None) -> None:
    """Re-enable a retired SLUG."""
    _set_scope_lifecycle(cli, slug, verb="enable", project=project)


def _set_scope_lifecycle(cli: CliContext, slug: str, *, verb: str, project: str | None) -> None:
    operation = f"POST /scopes/{{slug}}/{verb}"
    resp = cli.post(
        project_path(project, f"/scopes/{slug}/{verb}"),
        operation,
        json_body={},
        door=True,
    )
    raw, body = cli.decode(resp, _SCOPE, operation)
    state = "retired" if body.retired else "enabled"
    cli.show_lines(raw, f"scope {slug} is now {state}")
