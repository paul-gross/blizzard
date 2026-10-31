"""``blizzard hub repo`` — operator verbs over repository records."""

from __future__ import annotations

from typing import Any

import click

from blizzard.cli.get_group import GetGroup
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.forge_warnings import echo_warnings
from blizzard.hub.cli.views import Listing

_CLEARABLE = click.Choice(["park-timeout"])
_POLICY = click.option(
    "--policy",
    "policy",
    default=None,
    metavar="POLICY",
    help="How the repository's changes land: merge-on-checks (the default for a new repository).",
)
_PARK_TIMEOUT = click.option(
    "--park-timeout",
    "park_timeout",
    type=int,
    default=None,
    metavar="HOURS",
    help="How long a parked change may wait, in hours; only a parked repository takes one.",
)
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
        return (
            f"{row['slug']}  {row['name']}  {row['repository_id']}  r{row['revision']}  {_state(row)}  "
            f"{row['owner']}/{row['repo']}  {row['base_branch']}"
        )


def _refusals(repository: str) -> dict[int, str]:
    return {
        404: f"unknown repository {repository}",
        409: f"repository {repository} cannot be changed",
        422: "invalid repository",
    }


@click.group("repo", cls=GetGroup)
def repo_group() -> None:
    """Manage the tenant's repository records."""


@repo_group.command("create", cls=FleetCommand)
@click.argument("slug")
@click.option("--name", default=None, help="The repository's display name (default: SLUG).")
@click.option("--forge", "forge", default=None, help="The forge the repository is on, by id or slug.")
@click.option(
    "--forge-api-url",
    "forge_api_url",
    default=None,
    help="Deprecated — use --forge. The forge's API origin, an http(s) URL, resolved to the forge holding it.",
)
@click.option("--owner", required=True, help="The repository's owner on the forge.")
@click.option("--repo", "repo", required=True, help="The repository's name on the forge.")
@click.option("--base-branch", "base_branch", required=True, help="The branch work lands on.")
@click.option("--secret", "secret", required=True, help="The stored secret holding the forge token.")
@_POLICY
@_PARK_TIMEOUT
def repo_create(
    cli: CliContext,
    slug: str,
    name: str | None,
    forge: str | None,
    forge_api_url: str | None,
    owner: str,
    repo: str,
    base_branch: str,
    secret: str,
    policy: str | None,
    park_timeout: int | None,
) -> None:
    """Add repository SLUG at revision 1, named NAME (default: SLUG), on the forge --forge names.

    Exactly one of --forge and --forge-api-url names the forge; --forge-api-url is the old way, resolved to the forge
    holding that origin — made when none does — and warned about.

    Refused when SLUG is another repository's current slug, when the forge, owner and repo are already taken — a
    retired repository keeps its claim — when the secret is missing or retired, or when the forge cannot honour
    --policy. The hub resolves a chunk's commits against the record on its next deliver."""
    if (forge is None) == (forge_api_url is None):
        raise click.UsageError("name the forge with exactly one of --forge and --forge-api-url")
    named = {"forge": forge} if forge is not None else {"forge_api_url": forge_api_url}
    body = {
        "slug": slug,
        "name": name if name is not None else slug,
        **named,
        "owner": owner,
        "repo": repo,
        "base_branch": base_branch,
        "secret_name": secret,
        **_landing(policy, park_timeout),
    }
    resp = cli.post("/api/repositories", "POST /repositories", json_body=body, on_status=_refusals(slug), door=True)
    out = resp.json()
    echo_warnings(out)
    cli.show_lines(out, f"repository {out['slug']} created at revision 1 — id {out['repository_id']}")


@repo_group.command("list", cls=FleetCommand)
@click.option(
    "--include-retired", "include_retired", is_flag=True, default=False, help="Also list retired repositories."
)
def repo_list(cli: CliContext, include_retired: bool) -> None:
    """List every repository — slug, name, id, revision, state, owner/repo, base branch."""
    params = {"include_retired": "true"} if include_retired else None
    rows = cli.get("/api/repositories", "GET /repositories", params=params).json()["repositories"]
    cli.show(rows, RepoListing(rows))


@repo_group.command("get", cls=FleetCommand)
@click.argument("repository")
def repo_show(cli: CliContext, repository: str) -> None:
    """Show REPOSITORY — named by its id, its slug, or a slug it has left — with its fields, revision, and who created
    it and when."""
    body = cli.get(
        f"/api/repositories/{repository}",
        "GET /repositories/{repository}",
        on_status={404: f"unknown repository {repository}"},
    ).json()
    cli.show_lines(
        body,
        f"{body['slug']}  {body['name']}  revision {body['revision']}  {_state(body)}",
        f"id {body['repository_id']}",
        f"forge {body['forge_slug']}  {body['forge_api_url']}  {body['owner']}/{body['repo']}",
        f"base_branch {body['base_branch']}  secret {body['secret_name']}",
        f"policy {body['landing_policy']}{_timeout(body)}",
        f"created {body['created_at']} by {body['created_by']}",
    )


@repo_group.command("edit", cls=FleetCommand)
@click.argument("repository")
@click.option("--slug", default=None, help="The new slug; the one it leaves keeps naming the repository.")
@click.option("--name", default=None, help="The new display name.")
@click.option("--forge", "forge", default=None, help="The forge to move the repository to, by id or slug.")
@click.option(
    "--forge-api-url", "forge_api_url", default=None, help="Deprecated — use --forge. The new forge API origin."
)
@click.option("--owner", default=None, help="The new owner.")
@click.option("--repo", "repo", default=None, help="The new repository name on the forge.")
@click.option("--base-branch", "base_branch", default=None, help="The new base branch.")
@click.option("--secret", "secret", default=None, help="The new forge-token secret.")
@_POLICY
@_PARK_TIMEOUT
@click.option(
    "--clear",
    "clear",
    multiple=True,
    type=_CLEARABLE,
    help="Clear a field; repeatable. Clearing park-timeout sets it back to its default.",
)
@_IF_MATCH
def repo_edit(
    cli: CliContext,
    repository: str,
    slug: str | None,
    name: str | None,
    forge: str | None,
    forge_api_url: str | None,
    owner: str | None,
    repo: str | None,
    base_branch: str | None,
    secret: str | None,
    policy: str | None,
    park_timeout: int | None,
    clear: tuple[str, ...],
    if_match: int | None,
) -> None:
    """Change only the fields given on REPOSITORY; every other field keeps its value.

    A slug the repository leaves keeps naming it until another repository takes it. A change that leaves every field
    as it is writes nothing and keeps the revision. Refused when the new slug is another repository's, a field is
    given both a value and --clear, or --if-match names a stale revision."""
    given = {
        "slug": slug,
        "name": name,
        "forge": forge,
        "forge_api_url": forge_api_url,
        "owner": owner,
        "repo": repo,
        "base_branch": base_branch,
        "secret_name": secret,
        **_landing(policy, park_timeout),
    }
    body: dict[str, Any] = {k: v for k, v in given.items() if v is not None}
    for field in clear:
        key = field.replace("-", "_") + "_hours"
        if key in body:
            raise click.UsageError(f"--{field} and --clear {field} contradict each other")
        body[key] = None
    if not body:
        raise click.UsageError("nothing to change — give a field to set or --clear")
    resp = cli.patch(
        f"/api/repositories/{repository}",
        "PATCH /repositories/{repository}",
        json_body=body,
        on_status=_refusals(repository),
        if_match=if_match,
        door=True,
    )
    out = resp.json()
    echo_warnings(out)
    cli.show_lines(out, f"repository {out['slug']} is at revision {out['revision']}")


def _landing(policy: str | None, park_timeout: int | None) -> dict[str, Any]:
    """The landing fields of a write body, for the options given."""
    stated: dict[str, Any] = {}
    if policy is not None:
        stated["landing_policy"] = policy
    if park_timeout is not None:
        stated["park_timeout_hours"] = park_timeout
    return stated


def _timeout(body: Any) -> str:
    hours = body.get("park_timeout_hours")
    return "" if hours is None else f"  park timeout {hours}h"


@repo_group.command("retire", cls=FleetCommand)
@click.argument("repository")
@_IF_MATCH
def repo_retire(cli: CliContext, repository: str, if_match: int | None) -> None:
    """Retire REPOSITORY: it leaves the default list and keeps its coordinate, so no other repository takes it."""
    _set_lifecycle(cli, repository, "retire", if_match)


@repo_group.command("enable", cls=FleetCommand)
@click.argument("repository")
@_IF_MATCH
def repo_enable(cli: CliContext, repository: str, if_match: int | None) -> None:
    """Re-enable a retired REPOSITORY. Refused while its secret is retired."""
    _set_lifecycle(cli, repository, "enable", if_match)


def _set_lifecycle(cli: CliContext, repository: str, verb: str, if_match: int | None) -> None:
    resp = cli.post(
        f"/api/repositories/{repository}/{verb}",
        f"POST /repositories/{{repository}}/{verb}",
        on_status=_refusals(repository),
        if_match=if_match,
        door=True,
    )
    body = resp.json()
    cli.show_lines(body, f"repository {body['slug']} is now {_state(body)} at revision {body['revision']}")
