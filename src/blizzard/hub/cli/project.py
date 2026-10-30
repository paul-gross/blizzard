"""``blizzard hub project`` — operator verbs over a tenant's projects and the work sources and repositories each one
is linked to."""

from __future__ import annotations

from typing import Any

import click

from blizzard.cli.get_group import GetGroup
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing

_IF_MATCH = click.option(
    "--if-match",
    "if_match",
    type=int,
    default=None,
    help="Refuse the change unless it is still at this revision.",
)

_COLOR = click.option(
    "--color",
    "color",
    default=None,
    metavar="NAME|#RRGGBB",
    help=(
        "The project's color on the board, by palette name or '#rrggbb' hex (quote the hex in a shell); "
        "the hub picks the least-used palette color when omitted."
    ),
)

_SOURCE = click.option("--source", "source", default=None, metavar="SOURCE", help="A work source, by id or slug.")
_REPOSITORY = click.option(
    "--repository", "repository", default=None, metavar="REPOSITORY", help="A repository, by name."
)


class ProjectListing(Listing):
    empty = "no projects"

    def line(self, row: Any) -> str:
        return f"{row['slug']}  {row['name']}  {row['color']}  {row['project_id']}  r{row['revision']}"


def _refusals(project: str) -> dict[int, str]:
    return {404: f"no project {project}", 409: f"project {project} cannot be changed", 422: "project refused"}


def _linked(source_links: list[Any], repository_links: list[Any]) -> list[str]:
    sources = [
        f"{row['name']} (built in)" if row.get("built_in") else row.get("slug") or row["name"] for row in source_links
    ]
    repositories = [row["slug"] for row in repository_links]
    return [f"sources {', '.join(sources) or '-'}", f"repositories {', '.join(repositories) or '-'}"]


def _target(source: str | None, repository: str | None) -> tuple[str, str, str]:
    """The link collection, the record's name and its kind in operator words, for exactly one of the two options."""
    if (source is None) == (repository is None):
        raise click.UsageError("name exactly one of --source or --repository")
    if source is not None:
        return "source-links", source, "work source"
    assert repository is not None
    return "repository-links", repository, "repository"


@click.group("project", cls=GetGroup)
def project_group() -> None:
    """Manage the tenant's projects and the work sources and repositories linked to them."""


@project_group.command("create", cls=FleetCommand)
@click.argument("slug")
@click.option("--name", default=None, help="The project's display name (default: SLUG).")
@click.option("--description", default="", help="What the project is for.")
@_COLOR
def project_create(cli: CliContext, slug: str, name: str | None, description: str, color: str | None) -> None:
    """Create project SLUG at revision 1, named NAME (default: SLUG). It draws work only from the built-in hub
    source until a work source or repository is linked to it. Refused when SLUG is another project's current slug."""
    body: dict[str, Any] = {"slug": slug, "name": name if name is not None else slug, "description": description}
    if color is not None:
        body["color"] = color
    resp = cli.post("/api/projects", "POST /projects", json_body=body, on_status=_refusals(slug), door=True)
    out = resp.json()
    cli.show_lines(out, f"project {out['slug']} created at revision 1 — id {out['project_id']}")


@project_group.command("list", cls=FleetCommand)
def project_list(cli: CliContext) -> None:
    """List the tenant's projects — slug, name, color, id, revision."""
    rows = cli.get("/api/projects", "GET /projects").json()["projects"]
    cli.show(rows, ProjectListing(rows))


@project_group.command("get", cls=FleetCommand)
@click.argument("project")
def project_show(cli: CliContext, project: str) -> None:
    """Show PROJECT — named by its id, its slug, or a slug it has left — with the work sources and repositories
    linked to it."""
    missing = {404: f"no project {project}"}
    body = cli.get(f"/api/projects/{project}", "GET /projects/{project}", on_status=missing).json()
    sources = cli.get(
        f"/api/projects/{project}/source-links", "GET /projects/{project}/source-links", on_status=missing
    ).json()["source_links"]
    repositories = cli.get(
        f"/api/projects/{project}/repository-links", "GET /projects/{project}/repository-links", on_status=missing
    ).json()["repository_links"]
    cli.show_lines(
        {**body, "source_links": sources, "repository_links": repositories},
        f"{body['slug']}  {body['name']}  revision {body['revision']}",
        f"id {body['project_id']}  created {body['created_at']}",
        f"color {body['color']}",
        f"description {body['description'] or '-'}",
        *_linked(sources, repositories),
    )


@project_group.command("edit", cls=FleetCommand)
@click.argument("project")
@click.option("--slug", default=None, help="The new slug; the one it leaves keeps naming the project.")
@click.option("--name", default=None, help="The new display name.")
@click.option("--description", default=None, help="The new description.")
@_COLOR
@_IF_MATCH
def project_edit(
    cli: CliContext,
    project: str,
    slug: str | None,
    name: str | None,
    description: str | None,
    color: str | None,
    if_match: int | None,
) -> None:
    """Change only the fields given on PROJECT; every other field keeps its value. A slug the project leaves keeps
    naming it until another project takes it. A change that leaves every field as it is writes nothing and keeps the
    revision. Refused when the new slug is another project's, or --if-match names a stale revision."""
    given = {"slug": slug, "name": name, "description": description, "color": color}
    body = {k: v for k, v in given.items() if v is not None}
    if not body:
        raise click.UsageError("nothing to change — give a field to set")
    resp = cli.patch(
        f"/api/projects/{project}",
        "PATCH /projects/{project}",
        json_body=body,
        on_status=_refusals(project),
        if_match=if_match,
        door=True,
    )
    out = resp.json()
    cli.show_lines(out, f"project {out['slug']} is at revision {out['revision']}")


@project_group.command("link", cls=FleetCommand)
@click.argument("project")
@_SOURCE
@_REPOSITORY
@_IF_MATCH
def project_link(
    cli: CliContext, project: str, source: str | None, repository: str | None, if_match: int | None
) -> None:
    """Link PROJECT to one work source (--source) or one repository (--repository): work ingested from that source
    can belong to PROJECT, and PROJECT's work can land in that repository. A retired record can be linked; a link
    already standing, and the built-in hub source, change nothing; a link unlinked earlier is linked again."""
    collection, record, kind = _target(source, repository)
    resp = cli.put(
        f"/api/projects/{project}/{collection}/{record}",
        f"PUT /projects/{{project}}/{collection}/{{name}}",
        on_status={
            404: f"no project {project} or no {kind} {record}",
            409: f"the link to {kind} {record} is at another revision",
        },
        if_match=if_match,
        door=True,
    )
    cli.show_lines(resp.json(), f"project {project} is linked to {kind} {record}")


@project_group.command("unlink", cls=FleetCommand)
@click.argument("project")
@_SOURCE
@_REPOSITORY
@_IF_MATCH
def project_unlink(
    cli: CliContext, project: str, source: str | None, repository: str | None, if_match: int | None
) -> None:
    """Unlink PROJECT from one work source (--source) or one repository (--repository): new work from that source
    no longer goes to PROJECT, and PROJECT's work no longer lands in that repository. Unlinking an unlinked link
    changes nothing; link it again to restore it. Refused for the built-in hub source, which every project draws
    from."""
    collection, record, kind = _target(source, repository)
    resp = cli.post(
        f"/api/projects/{project}/{collection}/{record}/retire",
        f"POST /projects/{{project}}/{collection}/{{name}}/retire",
        on_status={
            404: f"project {project} was never linked to {kind} {record}",
            409: f"the link to {kind} {record} is at another revision",
            422: f"{kind} {record} cannot be unlinked",
        },
        if_match=if_match,
        door=True,
    )
    cli.show_lines(resp.json(), f"project {project} is unlinked from {kind} {record}")
