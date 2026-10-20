"""``blizzard hub invite`` — a tenant's invitations: issue one (its link printed this once), list them, revoke one."""

from __future__ import annotations

from typing import Any

import click

from blizzard.foundation.invitations import DEFAULT_LIFETIME_DAYS, MAX_LIFETIME_DAYS, MIN_LIFETIME_DAYS
from blizzard.hub.auth.memberships import MEMBERSHIP_ROLES
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing


class InvitationListing(Listing):
    """Each invitation's email, role, issuer and state — never its link, which the hub does not keep."""

    empty = "no invitations"

    def line(self, row: Any) -> str:
        state = row["state"]
        if row.get("accepted_by"):
            state = f"{state} by {row['accepted_by']}"
        return (
            f"{row['invitation_id']}  {row['email']}  {row['role']}  {state}  issued by {row['created_by']}"
            f"  expires_at={row['expires_at']}"
        )


@click.group("invite")
def invite_group() -> None:
    """A tenant's invitations: create one and hand its link on, list them, revoke one."""


@invite_group.command("create", cls=FleetCommand)
@click.option("--email", required=True, help="The one address that may accept the invitation.")
@click.option(
    "--role", required=True, type=click.Choice([r.value for r in MEMBERSHIP_ROLES]), help="The role it admits as."
)
@click.option(
    "--expires",
    "expires_in",
    type=click.IntRange(MIN_LIFETIME_DAYS, MAX_LIFETIME_DAYS),
    default=DEFAULT_LIFETIME_DAYS,
    show_default=True,
    metavar="DAYS",
    help="Whole days the invitation stays live.",
)
def invite_create(cli: CliContext, email: str, role: str, expires_in: int) -> None:
    """Invite --email into the tenant as --role and print the invitation's link. The link is shown this once — the hub
    keeps only its hash, so it cannot be printed again — and the hub sends no mail: hand it to the person yourself."""
    resp = cli.post(
        "/api/invitations",
        "POST /invitations",
        json_body={"email": email, "role": role, "expires_in": expires_in},
        on_status={422: "invitation refused"},
    )
    body = resp.json()
    invitation = body["invitation"]
    cli.show_lines(
        body,
        f"invited {invitation['email']} as {invitation['role']} — {invitation['invitation_id']}, "
        f"live until {invitation['expires_at']}",
        f"{cli.hub_url.rstrip('/')}/invite/{body['token']}",
        "this link is shown once and cannot be printed again: hand it to the person it invites",
    )


@invite_group.command("list", cls=FleetCommand)
def invite_list(cli: CliContext) -> None:
    """Every invitation of the tenant, oldest first: email, role, state (live, accepted by whom, expired, revoked),
    who issued it — never its link."""
    body = cli.get("/api/invitations", "GET /invitations").json()
    cli.show(body, InvitationListing(body["invitations"]))


@invite_group.command("revoke", cls=FleetCommand)
@click.argument("invitation_id")
def invite_revoke(cli: CliContext, invitation_id: str) -> None:
    """Revoke INVITATION_ID, live or expired, so its link admits nobody; an accepted one stays accepted."""
    resp = cli.post(
        f"/api/invitations/{invitation_id}/revocations",
        "POST /invitations/{id}/revocations",
        json_body={},
        on_status={404: f"no such invitation {invitation_id}", 409: "already accepted or revoked"},
    )
    body = resp.json()
    cli.show_lines(body, f"revoked invitation {body['invitation_id']} for {body['email']}")
