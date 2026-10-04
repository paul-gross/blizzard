"""``blizzard hub secret`` — operator verbs over the write-only secret store.

``set`` reads the value from stdin only, so it never appears in argv, shell history, or
the process list; no verb prints a value."""

from __future__ import annotations

from typing import Any

import click
import httpx

from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing


class SecretListing(Listing):
    empty = "no secrets yet"

    def line(self, row: Any) -> str:
        marker = "retired" if row["retired"] else "enabled"
        return f"{row['name']}  r{row['revision']}  {marker}"


@click.group("secret")
def secret_group() -> None:
    """Operator verbs over the write-only secret store: set, list, show, retire, enable."""


@secret_group.command("set", cls=FleetCommand)
@click.argument("name")
def secret_set(cli: CliContext, name: str) -> None:
    """Store NAME's value, read from stdin — never an argument. Replaces an existing secret.

    Trailing CR/LF is stripped; an empty value is refused."""
    value = click.get_text_stream("stdin").read().rstrip("\r\n")
    if not value:
        raise click.ClickException("refusing an empty secret value — pipe the value on stdin")
    resp = cli.send("put", f"/api/secrets/{name}/value", json_body={"value": value})
    if resp.status_code == httpx.codes.NOT_FOUND:
        resp = cli.post("/api/secrets", "POST /secrets", json_body={"name": name, "value": value})
    else:
        cli.check(resp, "PUT /secrets/{name}/value", on_status={409: f"secret {name} cannot be replaced"})
    body = resp.json()
    cli.show_lines(body, f"secret {name} set at revision {body['revision']}")


@secret_group.command("list", cls=FleetCommand)
@click.option("--include-retired", "include_retired", is_flag=True, default=False, help="Also list retired secrets.")
def secret_list(cli: CliContext, include_retired: bool) -> None:
    """List secrets — name, revision, retired. Never a value."""
    params = {"include_retired": "true"} if include_retired else None
    rows = cli.get("/api/secrets", "GET /secrets", params=params).json()
    cli.show(rows, SecretListing(rows))


@secret_group.command("show", cls=FleetCommand)
@click.argument("name")
def secret_show(cli: CliContext, name: str) -> None:
    """Show NAME's metadata — revision, who replaced it and when. Never its value."""
    body = cli.get(f"/api/secrets/{name}", "GET /secrets/{name}", on_status={404: f"unknown secret {name}"}).json()
    state = "retired" if body["retired"] else "enabled"
    cli.show_lines(
        body,
        f"{body['name']}  revision {body['revision']}  {state}",
        f"created {body['created_at']}",
        f"replaced {body['replaced_at']} by {body['replaced_by']}",
    )


@secret_group.command("retire", cls=FleetCommand)
@click.argument("name")
def secret_retire(cli: CliContext, name: str) -> None:
    """Retire NAME — a reversible brake; a retired secret cannot be replaced."""
    _set_lifecycle(cli, name, verb="retire")


@secret_group.command("enable", cls=FleetCommand)
@click.argument("name")
def secret_enable(cli: CliContext, name: str) -> None:
    """Re-enable a retired NAME."""
    _set_lifecycle(cli, name, verb="enable")


def _set_lifecycle(cli: CliContext, name: str, *, verb: str) -> None:
    resp = cli.post(
        f"/api/secrets/{name}/{verb}", f"POST /secrets/{{name}}/{verb}", on_status={404: f"unknown secret {name}"}
    )
    body = resp.json()
    state = "retired" if body.get("retired") else "enabled"
    cli.show_lines(body, f"secret {name} is now {state}")
