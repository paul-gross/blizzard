"""``blizzard hub urgency`` — operator commands over the tenant's urgencies, each addressed by its slug.

The set is ordered, most urgent first, and one live urgency is the tenant's default. Retiring an urgency open work
carries is refused unless ``--move-to`` names where that work goes; the refusal is printed with the command to run."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, cast

import click

from blizzard.cli.get_group import GetGroup
from blizzard.cli.refusal import refusal_of
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing
from blizzard.wire.problem_types_admin import URGENCY_IN_USE

#: The icon media types the hub accepts, by the file's suffix.
_ICON_TYPES = {".png": "image/png", ".webp": "image/webp"}


def _mark(row: Any) -> str:
    if row.get("emoji"):
        return f"  {row['emoji']}"
    if row.get("icon"):
        return "  icon"
    return ""


def _place(row: Any) -> str:
    return "retired" if row["place"] is None else f"{row['place']}."


class UrgencyListing(Listing):
    empty = "no urgencies"

    def line(self, row: Any) -> str:
        default = "  default" if row["default"] else ""
        return (
            f"{_place(row)}  {row['slug']}  name={row['name']}  color={row['color']}{_mark(row)}  "
            f"open={row['open_chunks']}{default}"
        )


@click.group("urgency", cls=GetGroup)
def urgency_group() -> None:
    """Manage the tenant's urgencies — the ordered set every chunk takes one of."""


def _icon_document(path: Path) -> dict[str, str]:
    media_type = _ICON_TYPES.get(path.suffix.lower())
    if media_type is None:
        raise click.BadParameter("an icon is a .png or .webp file", param_hint="--icon")
    return {"media_type": media_type, "data": base64.b64encode(path.read_bytes()).decode("ascii")}


def _show_one(cli: CliContext, body: Any, headline: str) -> None:
    default = "  default" if body["default"] else ""
    cli.show_lines(
        body,
        f"{headline}  {body['slug']}  {body['urgency_id']}{default}",
        f"name {body['name']}",
        f"color {body['color']}",
        f"mark {_mark(body).strip() or '-'}",
        f"place {_place(body)}",
        f"open chunks {body['open_chunks']}",
        f"revision {body['revision']}",
    )


def _show_order(cli: CliContext, body: Any) -> None:
    cli.show(body, UrgencyListing(body["urgencies"]))


_ICON = click.Path(exists=True, dir_okay=False, path_type=Path)


@urgency_group.command("create", cls=FleetCommand)
@click.argument("slug")
@click.option("--name", "display_name", default=None, help="The urgency's display name; defaults to the slug.")
@click.option("--color", default=None, help="A palette color or #rrggbb; defaults to the palette color least used.")
@click.option("--emoji", default=None, help="The urgency's mark: one emoji.")
@click.option("--icon", type=_ICON, default=None, help="The urgency's mark: a small .png or .webp file.")
@click.option("--after", default=None, metavar="SLUG", help="Place it just after this urgency.")
@click.option("--first", is_flag=True, default=False, help="Place it first, the most urgent.")
def urgency_create(
    cli: CliContext,
    slug: str,
    display_name: str | None,
    color: str | None,
    emoji: str | None,
    icon: Path | None,
    after: str | None,
    first: bool,
) -> None:
    """Create the urgency SLUG, at the least urgent end unless --after or --first places it.

    SLUG is the urgency's immutable handle; a slug a retired urgency holds is taken."""
    if emoji is not None and icon is not None:
        raise click.UsageError("give at most one of --emoji or --icon")
    if after is not None and first:
        raise click.UsageError("give at most one of --after or --first")
    body: dict[str, Any] = {"slug": slug}
    for field, value in (("name", display_name), ("color", color), ("emoji", emoji), ("after", after)):
        if value is not None:
            body[field] = value
    if icon is not None:
        body["icon"] = _icon_document(icon)
    if first:
        body["after"] = None
    resp = cli.post("/api/urgencies", "POST /urgencies", json_body=body, door=True)
    _show_one(cli, resp.json(), "created urgency")


@urgency_group.command("list", cls=FleetCommand)
@click.option("--include-retired", is_flag=True, default=False, help="Include retired urgencies.")
def urgency_list(cli: CliContext, include_retired: bool) -> None:
    """List the urgencies in order, most urgent first, the default marked, each with its open-chunk count."""
    params = {"retired": "true"} if include_retired else None
    _show_order(cli, cli.get("/api/urgencies", "GET /urgencies", params=params).json())


@urgency_group.command("get", cls=FleetCommand)
@click.argument("slug")
def urgency_get(cli: CliContext, slug: str) -> None:
    """Show SLUG's fields, its place in the order, and how many open chunks carry it."""
    _show_one(cli, cli.get(f"/api/urgencies/{slug}", "GET /urgencies/{key}").json(), "urgency")


@urgency_group.command("edit", cls=FleetCommand)
@click.argument("slug")
@click.option("--name", "display_name", default=None, help="The urgency's display name.")
@click.option("--color", default=None, help="A palette color or #rrggbb.")
@click.option("--emoji", default=None, help="Mark it with one emoji, replacing any icon.")
@click.option("--icon", type=_ICON, default=None, help="Mark it with a .png or .webp file, replacing any emoji.")
@click.option("--clear-mark", is_flag=True, default=False, help="Clear its emoji or icon.")
def urgency_edit(
    cli: CliContext,
    slug: str,
    display_name: str | None,
    color: str | None,
    emoji: str | None,
    icon: Path | None,
    clear_mark: bool,
) -> None:
    """Change SLUG's stated fields in place; its slug never changes. Editing to what it already holds writes nothing."""
    if sum((emoji is not None, icon is not None, clear_mark)) > 1:
        raise click.UsageError("give at most one of --emoji, --icon or --clear-mark")
    body: dict[str, Any] = {}
    if display_name is not None:
        body["name"] = display_name
    if color is not None:
        body["color"] = color
    if emoji is not None:
        body |= {"emoji": emoji, "icon": None}
    if icon is not None:
        body |= {"icon": _icon_document(icon), "emoji": None}
    if clear_mark:
        body |= {"emoji": None, "icon": None}
    if not body:
        raise click.UsageError("nothing to edit: give --name, --color, --emoji, --icon or --clear-mark")
    resp = cli.patch(f"/api/urgencies/{slug}", "PATCH /urgencies/{key}", json_body=body, door=True)
    _show_one(cli, resp.json(), "edited urgency")


def _move_to_command(cli: CliContext, slug: str) -> str:
    tenant = f" --tenant {cli.tenant}" if cli.tenant else ""
    return f"blizzard hub urgency retire {slug} --move-to <SLUG>{tenant}"


@urgency_group.command("retire", cls=FleetCommand)
@click.argument("slug")
@click.option("--move-to", default=None, metavar="SLUG", help="The live urgency SLUG's open chunks move to.")
def urgency_retire(cli: CliContext, slug: str, move_to: str | None) -> None:
    """Retire SLUG, moving the open chunks it carries to --move-to; finished chunks keep it.

    Refused for the tenant's default. Without --move-to, on an urgency open chunks carry, prints how many and the
    command to run. Moving open chunks needs chunk:control beside config:edit."""
    resp = cli.send(
        "post",
        f"/api/urgencies/{slug}/retire",
        json_body={"move_to": move_to} if move_to is not None else None,
        door=True,
    )
    refusal = refusal_of(resp)
    if refusal.type == URGENCY_IN_USE:
        detail = cast(dict[str, object], refusal.detail)
        count = detail["open_chunks"]
        raise click.ClickException(
            f"urgency {slug} carries {count} open chunks; name where they go:\n  {_move_to_command(cli, slug)}"
        )
    cli.check(resp, "POST /urgencies/{key}/retire")
    body = resp.json()
    cli.show_lines(body, f"retired urgency {slug}; moved {body['moved_chunks']} open chunks")


@urgency_group.command("enable", cls=FleetCommand)
@click.argument("slug")
def urgency_enable(cli: CliContext, slug: str) -> None:
    """Bring a retired SLUG back at its standing place in the order; no chunk moves back to it."""
    resp = cli.post(f"/api/urgencies/{slug}/enable", "POST /urgencies/{key}/enable", door=True)
    _show_one(cli, resp.json(), "enabled urgency")


@urgency_group.command("order", cls=FleetCommand)
@click.argument("urgencies", nargs=-1, required=True)
def urgency_order(cli: CliContext, urgencies: tuple[str, ...]) -> None:
    """Set the whole live order, most urgent first: URGENCIES names every live urgency once. Setting the order that
    already stands writes nothing."""
    resp = cli.put("/api/urgencies/order", "PUT /urgencies/order", json_body={"urgencies": list(urgencies)}, door=True)
    _show_order(cli, resp.json())


@urgency_group.command("position", cls=FleetCommand)
@click.argument("slug")
@click.option("--after", default=None, metavar="SLUG", help="Move it just after this urgency.")
@click.option("--first", is_flag=True, default=False, help="Move it first, the most urgent.")
def urgency_position(cli: CliContext, slug: str, after: str | None, first: bool) -> None:
    """Move the live urgency SLUG just after another (--after) or to the front (--first)."""
    if (after is not None) == first:
        raise click.UsageError("give exactly one of --after or --first")
    resp = cli.post(
        "/api/urgencies/order/position",
        "POST /urgencies/order/position",
        json_body={"urgency": slug, "after": after},
        door=True,
    )
    _show_order(cli, resp.json())
