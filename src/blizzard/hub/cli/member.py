"""``blizzard hub member`` — a tenant's members by user id: list them, grant or change a role, revoke."""

from __future__ import annotations

import click
from pydantic import TypeAdapter

from blizzard.hub.auth.memberships import MEMBERSHIP_ROLES
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing
from blizzard.wire.user import MemberView

_MEMBER = TypeAdapter(MemberView)
_MEMBERS = TypeAdapter(list[MemberView])


class MemberListing(Listing):
    empty = "no members"

    def line(self, row: MemberView) -> str:
        hub_admin = "  [hub admin]" if row.hub_admin else ""
        return f"{row.user_id}  {row.username}  {row.role}  email={row.email or '-'}{hub_admin}"


@click.group("member")
def member_group() -> None:
    """Manage a tenant's members, named by user id. Bringing in someone new is an invitation."""


@member_group.command("list", cls=FleetCommand)
def member_list(cli: CliContext) -> None:
    """Every member of the tenant: user id, username, role, email, and whether they are also a hub administrator."""
    raw, rows = cli.decode(cli.get("/api/members", "GET /members"), _MEMBERS, "GET /members")
    cli.show(raw, MemberListing(rows))


@member_group.command("grant", cls=FleetCommand)
@click.argument("user_id")
@click.argument("role", type=click.Choice([r.value for r in MEMBERSHIP_ROLES]))
def member_grant(cli: CliContext, user_id: str, role: str) -> None:
    """Give the member USER_ID the role ROLE in the tenant, or change the one they hold. USER_ID is a user id, never a
    username or an email; one who is not yet a member needs an invitation — a hub administrator may grant them
    directly. Nobody changes their own role, and the tenant's last admin keeps theirs."""
    resp = cli.put(
        f"/api/members/{user_id}",
        "PUT /members/{user_id}",
        json_body={"role": role},
    )
    raw, member = cli.decode(resp, _MEMBER, "PUT /members/{user_id}")
    cli.show_lines(raw, f"{member.user_id} ({member.username}) is now {member.role}")


@member_group.command("revoke", cls=FleetCommand)
@click.argument("user_id")
def member_revoke(cli: CliContext, user_id: str) -> None:
    """Revoke USER_ID's membership in the tenant. Nobody revokes their own, and the tenant's last admin keeps theirs."""
    cli.delete(
        f"/api/members/{user_id}",
        "DELETE /members/{user_id}",
    )
    cli.show_lines({"user_id": user_id, "revoked": True}, f"revoked {user_id}'s membership")
