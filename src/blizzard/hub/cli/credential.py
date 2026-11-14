"""``blizzard hub credential`` — operator verbs over credential records."""

from __future__ import annotations

from typing import Any

import click

from blizzard.cli.get_group import GetGroup
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing

_VARIANT = click.option(
    "--variant",
    type=click.Choice(["classic", "fine-grained"]),
    default=None,
    help="Which kind of GitHub token the secret holds; a fine-grained token is withheld what GitHub never grants it.",
)
_IF_MATCH = click.option(
    "--if-match", type=int, default=None, help="Refuse the change unless the credential is still at this revision."
)


def _state(row: Any) -> str:
    return "retired" if row["retired"] else "enabled"


class CredentialListing(Listing):
    empty = "no credentials yet"

    def line(self, row: Any) -> str:
        return (
            f"{row['slug']}  {row['name']}  {row['credential_id']}  r{row['revision']}  {_state(row)}  "
            f"{row['forge_slug']}  {row['binding']}  {row['authority']}"
        )


@click.group("credential", cls=GetGroup)
def credential_group() -> None:
    """Create, list, get, edit, retire and enable the credentials repositories and work sources act with."""


@credential_group.command("create", cls=FleetCommand)
@click.argument("slug")
@click.option("--name", default=None, help="The credential's display name; its slug when omitted.")
@click.option("--forge", required=True, help="The forge the credential acts on, by id or slug.")
@click.option("--binding", required=True, help="How the credential authenticates: github.token.")
@click.option("--secret", required=True, help="The stored secret the credential reads.")
@_VARIANT
def credential_create(
    cli: CliContext,
    slug: str,
    name: str | None,
    forge: str,
    binding: str,
    secret: str,
    variant: str | None,
) -> None:
    """Add credential SLUG at revision 1 on the forge --forge names.

    Refused when SLUG is another credential's, when another credential on the forge already acts as the same
    authority, or when the secret is missing or retired."""
    body = {
        "slug": slug,
        **({} if name is None else {"name": name}),
        "forge": forge,
        "binding": binding,
        "secret": secret,
        "settings": _settings(variant),
    }
    row = cli.post("/api/credentials", "POST /credentials", json_body=body, door=True).json()
    cli.show_lines(row, f"credential {row['slug']} created at revision 1 — id {row['credential_id']}")


@credential_group.command("list", cls=FleetCommand)
@click.option("--include-retired", is_flag=True, help="Also list retired credentials.")
def credential_list(cli: CliContext, include_retired: bool) -> None:
    """List credentials by slug with their id, revision, state, forge, binding and authority."""
    params = {"include_retired": "true"} if include_retired else None
    rows = cli.get("/api/credentials", "GET /credentials", params=params).json()["credentials"]
    cli.show(rows, CredentialListing(rows))


@credential_group.command("get", cls=FleetCommand)
@click.argument("credential")
def credential_show(cli: CliContext, credential: str) -> None:
    """Show CREDENTIAL — named by its id, its slug, or a slug it has left — with its fields and every repository and
    work source naming it."""
    row = cli.get(f"/api/credentials/{credential}", "GET /credentials/{credential}").json()
    cli.show_lines(
        row,
        f"{row['slug']}  {row['name']}  revision {row['revision']}  {_state(row)}",
        f"id {row['credential_id']}",
        f"forge {row['forge_slug']}  binding {row['binding']}  secret {row['secret_name']}",
        f"authority {row['authority']}",
        *(f"setting {key} {value}" for key, value in sorted(row["settings"].items())),
        f"created {row['created_at']} by {row['created_by']}",
        *(f"{r['kind']} {r['label']}  {r['key']}" for r in row["referrers"]),
    )


@credential_group.command("edit", cls=FleetCommand)
@click.argument("credential")
@click.option("--slug", default=None, help="The new slug; the one it leaves keeps naming the credential.")
@click.option("--name", default=None, help="The new display name.")
@click.option("--secret", default=None, help="The new secret; every record naming the credential follows it.")
@_VARIANT
@_IF_MATCH
def credential_edit(
    cli: CliContext,
    credential: str,
    slug: str | None,
    name: str | None,
    secret: str | None,
    variant: str | None,
    if_match: int | None,
) -> None:
    """Change only the fields given on CREDENTIAL; a no-op keeps its revision.

    Refused when the new slug is another credential's, when --if-match names a stale revision, or when the change
    would take away a capability an enabled repository acting with it needs."""
    body: dict[str, Any] = {k: v for k, v in {"slug": slug, "name": name, "secret": secret}.items() if v is not None}
    if variant is not None:
        body["settings"] = _settings(variant)
    if not body:
        raise click.UsageError("nothing to change — give a field to set")
    row = cli.patch(
        f"/api/credentials/{credential}",
        "PATCH /credentials/{credential}",
        json_body=body,
        if_match=if_match,
        door=True,
    ).json()
    cli.show_lines(row, f"credential {row['slug']} is at revision {row['revision']}")


@credential_group.command("retire", cls=FleetCommand)
@click.argument("credential")
@_IF_MATCH
def credential_retire(cli: CliContext, credential: str, if_match: int | None) -> None:
    """Retire CREDENTIAL, refusing while an enabled record names it."""
    _lifecycle(cli, credential, "retire", if_match)


@credential_group.command("enable", cls=FleetCommand)
@click.argument("credential")
@_IF_MATCH
def credential_enable(cli: CliContext, credential: str, if_match: int | None) -> None:
    """Re-enable a retired CREDENTIAL. Refused while its secret is retired."""
    _lifecycle(cli, credential, "enable", if_match)


def _settings(variant: str | None) -> dict[str, str]:
    return {} if variant is None else {"variant": variant}


def _lifecycle(cli: CliContext, credential: str, verb: str, if_match: int | None) -> None:
    row = cli.post(
        f"/api/credentials/{credential}/{verb}",
        f"POST /credentials/{{credential}}/{verb}",
        if_match=if_match,
        door=True,
    ).json()
    cli.show_lines(row, f"credential {row['slug']} is now {_state(row)} at revision {row['revision']}")
