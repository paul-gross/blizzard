"""``blizzard hub admin`` — the hub's administrators, by user id: list, grant, revoke."""

from __future__ import annotations

from typing import Any, cast

import click
from pydantic import TypeAdapter

from blizzard.hub.cli.admin_settings import admin_settings_group
from blizzard.hub.cli.command import AdminCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing
from blizzard.wire.user import HubAdminView

_ADMIN = TypeAdapter(HubAdminView)
_ADMINS = TypeAdapter(list[HubAdminView])


class HubAdminListing(Listing):
    empty = "no hub administrators"

    def line(self, row: Any) -> str:
        admin = cast(HubAdminView, row)
        return f"{admin.user_id}  {admin.username}  email={admin.email or '-'}"


@click.group("admin")
def admin_group() -> None:
    """Manage the hub's administrators, who create and delete tenants and can grant anyone into one."""


@admin_group.command("list", cls=AdminCommand)
def admin_list(cli: CliContext) -> None:
    """Every hub administrator: user id, username, email."""
    resp = cli.get("/api/admin/hub-admins", "GET /admin/hub-admins")
    raw, admins = cli.decode(resp, _ADMINS, "GET /admin/hub-admins")
    cli.show(raw, HubAdminListing(admins))


@admin_group.command("grant", cls=AdminCommand)
@click.argument("user_id")
def admin_grant(cli: CliContext, user_id: str) -> None:
    """Make USER_ID a hub administrator; it grants nothing inside any tenant by itself."""
    resp = cli.put(
        f"/api/admin/hub-admins/{user_id}",
        "PUT /admin/hub-admins/{user_id}",
    )
    raw, admin = cli.decode(resp, _ADMIN, "PUT /admin/hub-admins/{user_id}")
    cli.show_lines(raw, f"{admin.user_id} ({admin.username}) is a hub administrator")


@admin_group.command("revoke", cls=AdminCommand)
@click.argument("user_id")
def admin_revoke(cli: CliContext, user_id: str) -> None:
    """Revoke USER_ID's hub administrator role; the hub's last one keeps theirs."""
    cli.delete(
        f"/api/admin/hub-admins/{user_id}",
        "DELETE /admin/hub-admins/{user_id}",
    )
    cli.show_lines({"user_id": user_id, "revoked": True}, f"{user_id} is not a hub administrator")


admin_group.add_command(admin_settings_group)
