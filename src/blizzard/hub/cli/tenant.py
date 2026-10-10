"""``blizzard hub tenant`` — a hub administrator's verbs over the hub's tenants: create, list, rename, delete."""

from __future__ import annotations

from typing import Any

import click
import httpx

from blizzard.hub.cli.command import AdminCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing


class TenantListing(Listing):
    empty = "no tenants"

    def line(self, row: Any) -> str:
        if row["state"] == "deleting":
            return f"{row['tenant_id']}  {row['name']}  deleting since {row.get('closed_at') or '-'}"
        return f"{row['tenant_id']}  {row['name']}  open  created_at={row['created_at']}  by {row['created_by']}"


@click.group("tenant")
def tenant_group() -> None:
    """A hub administrator's verbs over the hub's tenants: create, list, rename, delete."""


@tenant_group.command("create", cls=AdminCommand)
@click.argument("name")
@click.option("--admin", "admin_user_id", default=None, metavar="USER_ID", help="Grant this user the admin role in it.")
def tenant_create(cli: CliContext, name: str, admin_user_id: str | None) -> None:
    """Create a tenant named NAME — its packaged graphs minted — usable the moment this answers. A name is a label: it
    is never refused for being taken, so the printed tenant id is what later verbs name it by."""
    resp = cli.post(
        "/api/admin/tenants",
        "POST /admin/tenants",
        json_body={"name": name, "admin_user_id": admin_user_id},
        on_status={404: f"no such user {admin_user_id}", 422: "name refused"},
        door=True,
    )
    body = resp.json()
    admin = f" — {admin_user_id} is its admin" if admin_user_id else ""
    cli.show_lines(body, f"created tenant {body['tenant_id']}  {body['name']}{admin}")


@tenant_group.command("list", cls=AdminCommand)
def tenant_list(cli: CliContext) -> None:
    """Every tenant the hub holds, oldest first: open ones, and those whose deletion is underway with when it began."""
    rows = cli.get("/api/admin/tenants", "GET /admin/tenants").json()
    cli.show(rows, TenantListing(rows))


@tenant_group.command("rename", cls=AdminCommand)
@click.argument("tenant_id")
@click.argument("name")
def tenant_rename(cli: CliContext, tenant_id: str, name: str) -> None:
    """Rename the open tenant TENANT_ID to NAME; its id does not change."""
    resp = cli.patch(
        f"/api/admin/tenants/{tenant_id}",
        "PATCH /admin/tenants/{id}",
        json_body={"name": name},
        on_status={404: f"no such tenant {tenant_id}", 422: "name refused"},
    )
    body = resp.json()
    cli.show_lines(body, f"tenant {body['tenant_id']} is now named {body['name']}")


@tenant_group.command("delete", cls=AdminCommand)
@click.argument("tenant_id")
@click.option("--yes", is_flag=True, default=False, help="Skip the confirmation prompt.")
def tenant_delete(cli: CliContext, tenant_id: str, yes: bool) -> None:
    """Delete TENANT_ID and every row it holds; it cannot be undone. The hub closes the tenant at once, so its people
    and runners lose it, and removes its rows: a small tenant within this call, a large one in the background — watch
    it with 'tenant list'. Asking again while a deletion is underway changes nothing. The hub's only open tenant is
    refused."""
    if not yes and not click.confirm(f"delete tenant {tenant_id} and every row it holds? this cannot be undone"):
        raise click.Abort()
    resp = cli.send("delete", f"/api/admin/tenants/{tenant_id}")
    if resp.status_code == httpx.codes.GONE:
        cli.show_lines(
            {"tenant_id": tenant_id, "state": "deleted", "rows_removed": None},
            f"tenant {tenant_id} was already deleted",
        )
        return
    cli.check(
        resp,
        "DELETE /admin/tenants/{id}",
        on_status={
            404: f"no such tenant {tenant_id}",
            409: "the hub's only open tenant cannot be deleted",
        },
    )
    body = resp.json()
    if resp.status_code == httpx.codes.ACCEPTED:
        cli.show_lines(
            body,
            f"tenant {tenant_id} is closed and its deletion is underway; the hub finishes it in the background — "
            "'blizzard hub tenant list' shows it until it is gone",
        )
        return
    cli.show_lines(body, f"deleted tenant {tenant_id} — {body['rows_removed']} rows removed")
