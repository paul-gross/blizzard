"""``blizzard hub source`` — operator verbs over work-source records."""

from __future__ import annotations

from typing import Any

import click

from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.forge_warnings import echo_warnings
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
        return (
            f"{row['slug']}  {row['name']}  {row['work_source_id']}  r{row['revision']}  {_state(row)}  "
            f"{row['provider']}  {row['locator']}"
        )


def _refusals(source: str) -> dict[int, str]:
    return {
        404: f"unknown work source {source}",
        409: f"work source {source} cannot be changed",
        422: "invalid work source",
    }


@click.group("source")
def source_group() -> None:
    """Manage the tenant's work sources, where work items come from."""


@source_group.command("create", cls=FleetCommand)
@click.argument("slug")
@click.option("--name", default=None, help="The source's display name (default: SLUG).")
@click.option("--provider", required=True, help="The provider the source reads items from, e.g. github.")
@click.option("--locator", required=True, help="Where the items live; for github, owner/name.")
@click.option("--secret", "secret", default=None, help="The stored secret holding the provider credential.")
@click.option("--forge", "forge", default=None, help="The forge the source reads from, by id or slug.")
@click.option(
    "--api-base",
    "api_base",
    default=None,
    help="Deprecated — use --forge. Puts the source on the forge holding this API origin, made when none does.",
)
@click.option(
    "--web-base",
    "web_base",
    default=None,
    help="Deprecated — use --forge. Must be that forge's web origin, or the create is refused; it sets nothing.",
)
@click.option(
    "--annotate/--no-annotate", "annotate", default=False, help="Whether the hub annotates items on the forge."
)
def source_create(
    cli: CliContext,
    slug: str,
    name: str | None,
    provider: str,
    locator: str,
    secret: str | None,
    forge: str | None,
    api_base: str | None,
    web_base: str | None,
    annotate: bool,
) -> None:
    """Add work source SLUG at revision 1, named NAME (default: SLUG), on the forge --forge names.

    Without --forge the source names its forge the old way — --api-base, or GitHub's API when it is left out — resolved
    to the forge holding that origin, made when none does, and warned about.

    Refused when SLUG is another source's current slug or ``hub`` (the built-in source's), when the provider and
    locator are already taken — a retired source keeps its claim — or when the secret is missing or retired. The hub
    reads the record on its next use of the source."""
    body = {
        "slug": slug,
        "name": name if name is not None else slug,
        "provider": provider,
        "locator": locator,
        "secret": secret,
        "annotate": annotate,
    }
    body.update({k: v for k, v in (("forge", forge), ("api_base", api_base), ("web_base", web_base)) if v is not None})
    resp = cli.post("/api/work-sources", "POST /work-sources", json_body=body, on_status=_refusals(slug), door=True)
    out = resp.json()
    echo_warnings(out)
    cli.show_lines(out, f"work source {out['slug']} created at revision 1 — id {out['work_source_id']}")


@source_group.command("list", cls=FleetCommand)
@click.option("--include-retired", "include_retired", is_flag=True, default=False, help="Also list retired sources.")
def source_list(cli: CliContext, include_retired: bool) -> None:
    """List the built-in hub source and every work source — slug, name, id, revision, state, provider, locator."""
    params = {"include_retired": "true"} if include_retired else None
    rows = cli.get("/api/work-sources", "GET /work-sources", params=params).json()["sources"]
    cli.show(rows, SourceListing(rows))


@source_group.command("show", cls=FleetCommand)
@click.argument("source")
def source_show(cli: CliContext, source: str) -> None:
    """Show SOURCE — named by its id, its slug, or a slug it has left — with its fields, revision, and who created it
    and when."""
    body = cli.get(
        f"/api/work-sources/{source}",
        "GET /work-sources/{source}",
        on_status={404: f"unknown work source {source}"},
    ).json()
    if body["built_in"]:
        cli.show_lines(body, f"{body['name']}  built-in  annotate={str(body['annotate']).lower()}")
        return
    cli.show_lines(
        body,
        f"{body['slug']}  {body['name']}  revision {body['revision']}  {_state(body)}",
        f"id {body['work_source_id']}",
        f"provider {body['provider']}  locator {body['locator']}",
        f"forge {body['forge_slug']}  api_base {body['api_base'] or '-'}  web_base {body['web_base'] or '-'}",
        f"secret {body['secret'] or '-'}  annotate {str(body['annotate']).lower()}",
        f"created {body['created_at']} by {body['created_by']}",
    )


@source_group.command("edit", cls=FleetCommand)
@click.argument("source")
@click.option("--slug", default=None, help="The new slug; the one it leaves keeps naming the source.")
@click.option("--name", default=None, help="The new display name.")
@click.option("--provider", default=None, help="The new provider.")
@click.option("--locator", default=None, help="The new locator.")
@click.option("--secret", default=None, help="The new credential secret.")
@click.option("--forge", "forge", default=None, help="The forge to move the source to, by id or slug.")
@click.option(
    "--api-base",
    "api_base",
    default=None,
    help="Deprecated — use --forge. Moves the source to the forge holding this API origin, made when none does.",
)
@click.option(
    "--web-base",
    "web_base",
    default=None,
    help="Deprecated — use --forge. Must be the source's forge's web origin, or the edit is refused; it sets nothing.",
)
@click.option("--annotate/--no-annotate", "annotate", default=None, help="Turn forge annotation on or off.")
@click.option(
    "--clear",
    "clear",
    multiple=True,
    type=_CLEARABLE,
    help=(
        "Clear a field; repeatable. Clearing secret unsets it; clearing api-base moves the source to GitHub's forge; "
        "clearing web-base changes nothing, since the web origin is the forge's, and is warned about."
    ),
)
@_IF_MATCH
def source_edit(
    cli: CliContext,
    source: str,
    slug: str | None,
    name: str | None,
    provider: str | None,
    locator: str | None,
    secret: str | None,
    forge: str | None,
    api_base: str | None,
    web_base: str | None,
    annotate: bool | None,
    clear: tuple[str, ...],
    if_match: int | None,
) -> None:
    """Change only the fields given on SOURCE; every other field keeps its value.

    A slug the source leaves keeps naming it until another source takes it. A change that leaves every field as it is
    writes nothing and keeps the revision. Refused when the new slug is another source's or ``hub``, when --if-match
    names a stale revision, and for the built-in hub source."""
    given = {
        "slug": slug,
        "name": name,
        "provider": provider,
        "locator": locator,
        "secret": secret,
        "forge": forge,
        "api_base": api_base,
        "web_base": web_base,
    }
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
        f"/api/work-sources/{source}",
        "PATCH /work-sources/{source}",
        json_body=body,
        on_status=_refusals(source),
        if_match=if_match,
        door=True,
    )
    out = resp.json()
    echo_warnings(out)
    cli.show_lines(out, f"work source {out['slug']} is at revision {out['revision']}")


@source_group.command("retire", cls=FleetCommand)
@click.argument("source")
@_IF_MATCH
def source_retire(cli: CliContext, source: str, if_match: int | None) -> None:
    """Retire SOURCE: it leaves the default list and keeps its provider and locator, so no other source takes them."""
    _set_lifecycle(cli, source, "retire", if_match)


@source_group.command("enable", cls=FleetCommand)
@click.argument("source")
@_IF_MATCH
def source_enable(cli: CliContext, source: str, if_match: int | None) -> None:
    """Re-enable a retired SOURCE. Refused while its secret is retired."""
    _set_lifecycle(cli, source, "enable", if_match)


def _set_lifecycle(cli: CliContext, source: str, verb: str, if_match: int | None) -> None:
    resp = cli.post(
        f"/api/work-sources/{source}/{verb}",
        f"POST /work-sources/{{source}}/{verb}",
        on_status=_refusals(source),
        if_match=if_match,
        door=True,
    )
    body = resp.json()
    cli.show_lines(body, f"work source {body['slug']} is now {_state(body)} at revision {body['revision']}")
