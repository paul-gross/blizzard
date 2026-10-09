"""``blizzard hub admin`` — the hub's administrators, by user id: list, grant, revoke."""

from __future__ import annotations

from typing import Any

import click

from blizzard.hub.cli.command import AdminCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing


class HubAdminListing(Listing):
    empty = "no hub administrators"

    def line(self, row: Any) -> str:
        return f"{row['user_id']}  {row['username']}  email={row.get('email') or '-'}"


@click.group("admin")
def admin_group() -> None:
    """The hub's administrators, who create and delete tenants and grant anyone into one: list, grant, revoke."""


@admin_group.command("list", cls=AdminCommand)
def admin_list(cli: CliContext) -> None:
    """Every hub administrator: user id, username, email."""
    rows = cli.get("/api/admin/hub-admins", "GET /admin/hub-admins").json()
    cli.show(rows, HubAdminListing(rows))


@admin_group.command("grant", cls=AdminCommand)
@click.argument("user_id")
def admin_grant(cli: CliContext, user_id: str) -> None:
    """Make USER_ID a hub administrator; it grants nothing inside any tenant by itself."""
    resp = cli.put(
        f"/api/admin/hub-admins/{user_id}",
        "PUT /admin/hub-admins/{user_id}",
        on_status={404: f"no such user {user_id}"},
    )
    body = resp.json()
    cli.show_lines(body, f"{body['user_id']} ({body['username']}) is a hub administrator")


@admin_group.command("revoke", cls=AdminCommand)
@click.argument("user_id")
def admin_revoke(cli: CliContext, user_id: str) -> None:
    """Revoke USER_ID's hub administrator role; the hub's last one keeps theirs."""
    cli.delete(
        f"/api/admin/hub-admins/{user_id}",
        "DELETE /admin/hub-admins/{user_id}",
        on_status={404: f"no such user {user_id}", 409: "refused"},
    )
    cli.show_lines({"user_id": user_id, "revoked": True}, f"{user_id} is not a hub administrator")
