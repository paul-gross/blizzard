"""``blizzard hub source`` — operator verbs over work-source records."""

from __future__ import annotations

from typing import Any

import click

from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing

_CLEARABLE = click.Choice(["api-base", "web-base", "secret"])
_IF_MATCH = click.option(
    "--if-match",
    "if_match",
    type=int,
    default=None,
    help="Refuse the change unless the source is still at this revision.",
)


def _state(row: Any) -> str:
    if row.get("built_in"):
        return "built-in"
    return "retired" if row["retired"] else "enabled"


class SourceListing(Listing):
    empty = "no work sources yet"

    def line(self, row: Any) -> str:
        if row.get("built_in"):
            return f"{row['name']}  built-in"
        return f"{row['name']}  r{row['revision']}  {_state(row)}  {row['provider']}  {row['locator']}"


def _refusals(name: str) -> dict[int, str]:
    return {
        404: f"unknown work source {name}",
        409: f"work source {name} cannot be changed",
        422: "invalid work source",
    }


@click.group("source")
def source_group() -> None:
    """Operator verbs over work-source records: create, list, show, edit, retire, enable."""


@source_group.command("create", cls=FleetCommand)
@click.argument("name")
@click.option("--provider", required=True, help="The provider the source reads items from, e.g. github.")
@click.option("--locator", required=True, help="Where the items live; for github, owner/name.")
@click.option("--secret", "secret", default=None, help="The stored secret holding the provider credential.")
@click.option("--api-base", "api_base", default=None, help="Provider API origin override.")
@click.option("--web-base", "web_base", default=None, help="Web origin override.")
@click.option(
    "--annotate/--no-annotate", "annotate", default=False, help="Whether the hub annotates items on the forge."
)
def source_create(
    cli: CliContext,
    name: str,
    provider: str,
    locator: str,
    secret: str | None,
    api_base: str | None,
    web_base: str | None,
    annotate: bool,
) -> None:
    """Add work source NAME at revision 1.

    Refused when the name or the provider and locator are already taken — a retired source keeps its claim —
    or when the secret is missing or retired. A stored source does not yet change what the hub ingests."""
    body = {
        "name": name,
        "provider": provider,
        "locator": locator,
        "secret": secret,
        "api_base": api_base,
        "web_base": web_base,
        "annotate": annotate,
    }
    resp = cli.post("/api/work-sources", "POST /work-sources", json_body=body, on_status=_refusals(name), door=True)
    cli.show_lines(resp.json(), f"work source {name} created at revision 1")


@source_group.command("list", cls=FleetCommand)
@click.option("--include-retired", "include_retired", is_flag=True, default=False, help="Also list retired sources.")
def source_list(cli: CliContext, include_retired: bool) -> None:
    """List the built-in hub source and every work source — name, revision, state, provider, locator."""
    params = {"include_retired": "true"} if include_retired else None
    rows = cli.get("/api/work-sources", "GET /work-sources", params=params).json()["sources"]
    cli.show(rows, SourceListing(rows))


@source_group.command("show", cls=FleetCommand)
@click.argument("name")
def source_show(cli: CliContext, name: str) -> None:
    """Show NAME's fields, revision, and who created it and when."""
    body = cli.get(
        f"/api/work-sources/{name}", "GET /work-sources/{name}", on_status={404: f"unknown work source {name}"}
    ).json()
    if body["built_in"]:
        cli.show_lines(body, f"{name}  built-in  annotate={str(body['annotate']).lower()}")
        return
    cli.show_lines(
        body,
        f"{name}  revision {body['revision']}  {_state(body)}",
        f"provider {body['provider']}  locator {body['locator']}",
        f"api_base {body['api_base'] or '-'}  web_base {body['web_base'] or '-'}",
        f"secret {body['secret'] or '-'}  annotate {str(body['annotate']).lower()}",
        f"created {body['created_at']} by {body['created_by']}",
    )


@source_group.command("edit", cls=FleetCommand)
@click.argument("name")
@click.option("--provider", default=None, help="The new provider.")
@click.option("--locator", default=None, help="The new locator.")
@click.option("--secret", default=None, help="The new credential secret.")
@click.option("--api-base", "api_base", default=None, help="The new API origin override.")
@click.option("--web-base", "web_base", default=None, help="The new web origin override.")
@click.option("--annotate/--no-annotate", "annotate", default=None, help="Turn forge annotation on or off.")
@click.option("--clear", "clear", multiple=True, type=_CLEARABLE, help="Clear a field back to unset; repeatable.")
@_IF_MATCH
def source_edit(
    cli: CliContext,
    name: str,
    provider: str | None,
    locator: str | None,
    secret: str | None,
    api_base: str | None,
    web_base: str | None,
    annotate: bool | None,
    clear: tuple[str, ...],
    if_match: int | None,
) -> None:
    """Change only the fields given on NAME; every other field keeps its value.

    A change that leaves every field as it is writes nothing and keeps the revision. Refused when
    --if-match names a stale revision, and for the built-in hub source."""
    given = {"provider": provider, "locator": locator, "secret": secret, "api_base": api_base, "web_base": web_base}
    body: dict[str, Any] = {k: v for k, v in given.items() if v is not None}
    if annotate is not None:
        body["annotate"] = annotate
    for field in clear:
        key = field.replace("-", "_")
        if key in body:
            raise click.UsageError(f"--{field} and --clear {field} contradict each other")
        body[key] = None
    if not body:
        raise click.UsageError("nothing to change — give a field to set or --clear")
    resp = cli.patch(
        f"/api/work-sources/{name}",
        "PATCH /work-sources/{name}",
        json_body=body,
        on_status=_refusals(name),
        if_match=if_match,
        door=True,
    )
    out = resp.json()
    cli.show_lines(out, f"work source {name} is at revision {out['revision']}")


@source_group.command("retire", cls=FleetCommand)
@click.argument("name")
@_IF_MATCH
def source_retire(cli: CliContext, name: str, if_match: int | None) -> None:
    """Retire NAME: it leaves the default list and keeps its provider and locator, so no other source takes them."""
    _set_lifecycle(cli, name, "retire", if_match)


@source_group.command("enable", cls=FleetCommand)
@click.argument("name")
@_IF_MATCH
def source_enable(cli: CliContext, name: str, if_match: int | None) -> None:
    """Re-enable a retired NAME. Refused while its secret is retired."""
    _set_lifecycle(cli, name, "enable", if_match)


def _set_lifecycle(cli: CliContext, name: str, verb: str, if_match: int | None) -> None:
    resp = cli.post(
        f"/api/work-sources/{name}/{verb}",
        f"POST /work-sources/{{name}}/{verb}",
        on_status=_refusals(name),
        if_match=if_match,
        door=True,
    )
    body = resp.json()
    cli.show_lines(body, f"work source {name} is now {_state(body)} at revision {body['revision']}")
