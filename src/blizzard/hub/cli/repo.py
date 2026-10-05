"""``blizzard hub repo`` — operator verbs over repository records."""

from __future__ import annotations

from typing import Any

import click

from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing

_IF_MATCH = click.option(
    "--if-match",
    "if_match",
    type=int,
    default=None,
    help="Refuse the change unless the repository is still at this revision.",
)


def _state(row: Any) -> str:
    return "retired" if row["retired"] else "enabled"


class RepoListing(Listing):
    empty = "no repositories yet"

    def line(self, row: Any) -> str:
        return f"{row['name']}  r{row['revision']}  {_state(row)}  {row['owner']}/{row['repo']}  {row['base_branch']}"


def _refusals(name: str) -> dict[int, str]:
    return {
        404: f"unknown repository {name}",
        409: f"repository {name} cannot be changed",
        422: "invalid repository",
    }


@click.group("repo")
def repo_group() -> None:
    """Operator verbs over repository records: create, list, show, edit, retire, enable."""


@repo_group.command("create", cls=FleetCommand)
@click.argument("name")
@click.option("--forge-api-url", "forge_api_url", required=True, help="The forge's API origin, an http(s) URL.")
@click.option("--owner", required=True, help="The repository's owner on the forge.")
@click.option("--repo", "repo", required=True, help="The repository's name on the forge.")
@click.option("--base-branch", "base_branch", required=True, help="The branch work lands on.")
@click.option("--secret", "secret", required=True, help="The stored secret holding the forge token.")
def repo_create(
    cli: CliContext, name: str, forge_api_url: str, owner: str, repo: str, base_branch: str, secret: str
) -> None:
    """Add repository NAME at revision 1.

    Refused when the name or the forge, owner and repo are already taken — a retired repository keeps its
    claim — or when the secret is missing or retired. The hub resolves a chunk's commits against the record
    on its next deliver."""
    body = {
        "name": name,
        "forge_api_url": forge_api_url,
        "owner": owner,
        "repo": repo,
        "base_branch": base_branch,
        "secret_name": secret,
    }
    resp = cli.post("/api/repositories", "POST /repositories", json_body=body, on_status=_refusals(name), door=True)
    cli.show_lines(resp.json(), f"repository {name} created at revision 1")


@repo_group.command("list", cls=FleetCommand)
@click.option(
    "--include-retired", "include_retired", is_flag=True, default=False, help="Also list retired repositories."
)
def repo_list(cli: CliContext, include_retired: bool) -> None:
    """List every repository — name, revision, state, owner/repo, base branch."""
    params = {"include_retired": "true"} if include_retired else None
    rows = cli.get("/api/repositories", "GET /repositories", params=params).json()["repositories"]
    cli.show(rows, RepoListing(rows))


@repo_group.command("show", cls=FleetCommand)
@click.argument("name")
def repo_show(cli: CliContext, name: str) -> None:
    """Show NAME's fields, revision, and who created it and when."""
    body = cli.get(
        f"/api/repositories/{name}", "GET /repositories/{name}", on_status={404: f"unknown repository {name}"}
    ).json()
    cli.show_lines(
        body,
        f"{name}  revision {body['revision']}  {_state(body)}",
        f"forge_api_url {body['forge_api_url']}  {body['owner']}/{body['repo']}",
        f"base_branch {body['base_branch']}  secret {body['secret_name']}",
        f"created {body['created_at']} by {body['created_by']}",
    )


@repo_group.command("edit", cls=FleetCommand)
@click.argument("name")
@click.option("--forge-api-url", "forge_api_url", default=None, help="The new forge API origin.")
@click.option("--owner", default=None, help="The new owner.")
@click.option("--repo", "repo", default=None, help="The new repository name on the forge.")
@click.option("--base-branch", "base_branch", default=None, help="The new base branch.")
@click.option("--secret", "secret", default=None, help="The new forge-token secret.")
@_IF_MATCH
def repo_edit(
    cli: CliContext,
    name: str,
    forge_api_url: str | None,
    owner: str | None,
    repo: str | None,
    base_branch: str | None,
    secret: str | None,
    if_match: int | None,
) -> None:
    """Change only the fields given on NAME; every other field keeps its value.

    A change that leaves every field as it is writes nothing and keeps the revision. Refused when
    --if-match names a stale revision."""
    given = {
        "forge_api_url": forge_api_url,
        "owner": owner,
        "repo": repo,
        "base_branch": base_branch,
        "secret_name": secret,
    }
    body = {k: v for k, v in given.items() if v is not None}
    if not body:
        raise click.UsageError("nothing to change — give a field to set")
    resp = cli.patch(
        f"/api/repositories/{name}",
        "PATCH /repositories/{name}",
        json_body=body,
        on_status=_refusals(name),
        if_match=if_match,
        door=True,
    )
    out = resp.json()
    cli.show_lines(out, f"repository {name} is at revision {out['revision']}")


@repo_group.command("retire", cls=FleetCommand)
@click.argument("name")
@_IF_MATCH
def repo_retire(cli: CliContext, name: str, if_match: int | None) -> None:
    """Retire NAME: it leaves the default list and keeps its coordinate, so no other repository takes it."""
    _set_lifecycle(cli, name, "retire", if_match)


@repo_group.command("enable", cls=FleetCommand)
@click.argument("name")
@_IF_MATCH
def repo_enable(cli: CliContext, name: str, if_match: int | None) -> None:
    """Re-enable a retired NAME. Refused while its secret is retired."""
    _set_lifecycle(cli, name, "enable", if_match)


def _set_lifecycle(cli: CliContext, name: str, verb: str, if_match: int | None) -> None:
    resp = cli.post(
        f"/api/repositories/{name}/{verb}",
        f"POST /repositories/{{name}}/{verb}",
        on_status=_refusals(name),
        if_match=if_match,
        door=True,
    )
    body = resp.json()
    cli.show_lines(body, f"repository {name} is now {_state(body)} at revision {body['revision']}")
