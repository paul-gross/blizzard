"""``blizzard hub runner-host`` — operator commands over one host, each addressed by its hub-minted id."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, TypedDict, cast

import click

from blizzard.cli.get_group import GetGroup
from blizzard.foundation.roles import dto
from blizzard.foundation.runner_connection import RunnerConnection
from blizzard.foundation.runner_token_names import HOST_TOKEN_VARIABLE
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Listing


class WorkspaceRow(TypedDict):
    workspace_id: str


class SubscriptionRow(TypedDict):
    slug: str


@dto
@dataclass(frozen=True)
class HostLine:
    row: dict[str, Any]
    #: The name column's width; a listing widens it to the longest name it shows.
    name_width: int = 16

    NAME_WIDTH = 16

    @property
    def name(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        return self.row.get("host_name") or "-"

    @property
    def connection(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        """``never-connected`` for a host added but never reported — else ``online`` or ``offline``."""
        if self.row.get("connection") == RunnerConnection.NEVER_CONNECTED:
            return "never-connected"
        return "online" if self.row.get("online") else "offline"

    @property
    def retirement(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        if not self.row.get("retired"):
            return ""
        return f" [retired {self.row.get('retired_at') or '-'} by {self.row.get('retired_by') or '-'}]"

    def line(self) -> str:
        runners = len(self.row.get("runner_ids") or [])
        return (
            f"{self.row['host_id']}  {self.name:<{self.name_width}} {self.connection:<15} runners={runners}"
            f"{self.retirement}"
        )


class HostListing(Listing):
    """Every host's line, the name column as wide as the longest name shown so the state column lines up."""

    empty = "no hosts — add one with `blizzard hub runner-host add <name>`"

    def line(self, row: Any) -> str:
        width = max([HostLine.NAME_WIDTH, *(len(HostLine(shown).name) for shown in self.rows)])
        return HostLine(row, name_width=width).line()


@dto
@dataclass(frozen=True)
class HostDetail:
    body: dict[str, Any]

    def lines(self) -> Iterator[str]:
        yield HostLine(self.body).line()
        yield f"  added={self.body.get('added_at') or '-'} by {self.body.get('added_by') or '-'}"
        yield f"  url={self.body.get('url') or '-'}  enrolled={self.body.get('enrolled')}"
        workspace_rows = cast(list[WorkspaceRow], self.body.get("workspaces") or [])
        workspaces = ", ".join(w["workspace_id"] for w in workspace_rows) or "-"
        yield f"  workspaces={workspaces}"
        subscription_rows = cast(list[SubscriptionRow], self.body.get("subscriptions") or [])
        subscriptions = ", ".join(s["slug"] for s in subscription_rows) or "-"
        yield f"  subscriptions={subscriptions}"
        yield f"  runners={', '.join(self.body.get('runner_ids') or []) or '-'}"


def token_lines(token: str) -> tuple[str, str]:
    """A freshly minted host bearer token, printed once as the line a host's ``.env`` takes."""
    return "bearer token, shown only once — put this line in the host's .env:", f"{HOST_TOKEN_VARIABLE}={token}"


@click.group("runner-host", cls=GetGroup)
def runner_host_group() -> None:
    """Register hosts, place runners under them, credential, and retire them.

    Every verb but `add` and `list` takes a host's hub-minted id, which `list` shows beside each name. A name is
    display-only and never resolves to a host. (`blizzard hub host` is the hub daemon's launcher.)"""


@runner_host_group.command("add", cls=FleetCommand)
@click.argument("name")
@click.option(
    "--adopt",
    "adopt",
    multiple=True,
    metavar="RUNNER_ID",
    help="A runner to place under the host (repeatable): refused when it is another tenant's, already under a host, "
    "or retired — and then no host is added.",
)
def host_add(cli: CliContext, name: str, adopt: tuple[str, ...]) -> None:
    """Add a host under NAME; prints its hub-minted id and bearer token exactly once.

    The host stays never connected until it first reports with that token. A name a live host already holds is
    refused."""
    resp = cli.send("post", "/api/hosts", json_body={"name": name, "adopt": list(adopt)})
    cli.check(
        resp,
        "POST /hosts",
        on_status={
            404: "an adopted runner is unknown to this tenant",
            409: f"cannot add host {name}: the name is held by a live host, or an adopted runner is retired or "
            "already under a host",
            422: "a host's name must not be blank",
        },
    )
    body = resp.json()
    lines = [f"added host {body['host_id']} ({body['host_name']}) — never connected"]
    if body.get("adopted"):
        lines.append(f"adopted: {', '.join(body['adopted'])}")
    cli.show_lines(body, *lines, *token_lines(body["token"]))


@runner_host_group.command("list", cls=FleetCommand)
@click.option("--all", "include_retired", is_flag=True, default=False, help="Include retired hosts, marked.")
def host_list(cli: CliContext, include_retired: bool) -> None:
    """The host registry — every added host, oldest first: its id, name, connection and runner count.

    A host added but never reported shows as never-connected. Excludes a retired host unless --all."""
    params = {"include_retired": "true"} if include_retired else None
    body = cli.get("/api/hosts", "GET /hosts", params=params).json()
    cli.show(body, HostListing(body.get("hosts", [])))


@runner_host_group.command("get", cls=FleetCommand)
@click.argument("host_id")
def host_show(cli: CliContext, host_id: str) -> None:
    """One host's connection, reported facts and runners, by HOST_ID — symmetric with ``runner-host list``."""
    resp = cli.get(f"/api/hosts/{host_id}", "GET /hosts/{id}", on_status={404: f"unknown host {host_id}"})
    body = resp.json()
    cli.show(body, HostDetail(body))


def _runners_verb(cli: CliContext, host_id: str, runner_ids: tuple[str, ...], *, verb: str, by: str) -> dict[str, Any]:
    resp = cli.post(
        f"/api/hosts/{host_id}/{verb}",
        f"POST /hosts/{{id}}/{verb}",
        json_body={"by": by, "runner_ids": list(runner_ids)},
        on_status={
            404: f"unknown host {host_id}, or a runner unknown to it",
            409: f"host {host_id} cannot take those runners: one is retired or already under a host, or the host is "
            "retired",
        },
    )
    body: dict[str, Any] = resp.json()
    return body


@runner_host_group.command("adopt", cls=FleetCommand)
@click.argument("host_id")
@click.argument("runner_ids", nargs=-1, required=True)
@click.option("--by", "by", default="operator", help="Who is adopting, recorded as the actor.")
def host_adopt(cli: CliContext, host_id: str, runner_ids: tuple[str, ...], by: str) -> None:
    """Place existing runners RUNNER_IDS under host HOST_ID, all or none.

    Refused for a runner of another tenant (as unknown), one already under a host, and a retired one."""
    body = _runners_verb(cli, host_id, runner_ids, verb="adoptions", by=by)
    cli.show_lines(body, f"adopted {', '.join(runner_ids)} under host {host_id}")


@runner_host_group.command("release", cls=FleetCommand)
@click.argument("host_id")
@click.argument("runner_ids", nargs=-1, required=True)
@click.option("--by", "by", default="operator", help="Who is releasing, recorded as the actor.")
def host_release(cli: CliContext, host_id: str, runner_ids: tuple[str, ...], by: str) -> None:
    """Return runners RUNNER_IDS under host HOST_ID to standing alone, keeping their ids and tokens.

    A registered host then halts each one, as a runner the hub no longer holds under it."""
    body = _runners_verb(cli, host_id, runner_ids, verb="releases", by=by)
    cli.show_lines(body, f"released {', '.join(runner_ids)} from host {host_id}")


@runner_host_group.command("revoke-token", cls=FleetCommand)
@click.argument("host_id")
@click.option("--by", "by", default="operator", help="Who is revoking, recorded as the actor.")
def host_revoke_token(cli: CliContext, host_id: str, by: str) -> None:
    """Revoke host HOST_ID's token and issue none — it stays added, and is refused until `enroll` issues a new one."""
    resp = cli.post(
        f"/api/hosts/{host_id}/token-revocations",
        "POST /hosts/{id}/token-revocations",
        json_body={"by": by},
        on_status={404: f"unknown host {host_id}", 409: f"host {host_id} has no enrolled token"},
    )
    cli.show_lines(resp.json(), f"revoked {host_id}'s token — enroll it to mint a fresh one")


@runner_host_group.command("enroll", cls=FleetCommand)
@click.argument("host_id")
def host_enroll(cli: CliContext, host_id: str) -> None:
    """Issue host HOST_ID a new bearer token; prints the plaintext exactly once.

    The previous token stops working immediately. Refused when HOST_ID is unknown or retired."""
    resp = cli.post(
        f"/api/hosts/{host_id}/enrollments",
        "POST /hosts/{id}/enrollments",
        on_status={404: f"unknown host {host_id}", 409: f"host {host_id} is retired"},
    )
    body = resp.json()
    cli.show_lines(body, f"enrolled {host_id} — its previous token no longer resolves", *token_lines(body["token"]))


@runner_host_group.command("retire", cls=FleetCommand)
@click.argument("host_id")
@click.option("--force", is_flag=True, default=False, help="Release every chunk the runners under the host hold.")
@click.option("--by", "by", default="operator", help="Who is retiring, recorded as the actor.")
def host_retire(cli: CliContext, host_id: str, force: bool, by: str) -> None:
    """Retire host HOST_ID — revoke its token and retire every runner under it.

    Refused while any of them holds chunks unless --force, which releases them. Re-running finishes a partial
    retirement. Stop the host process too: the hub refuses it from here on."""
    resp = cli.post(
        f"/api/hosts/{host_id}/retire",
        "POST /hosts/{id}/retire",
        json_body={"by": by, "force": force},
        on_status={404: f"unknown host {host_id}", 409: f"host {host_id} has runners holding chunks"},
    )
    body = resp.json()
    lines = [f"host {host_id} is retired"]
    released = cast(list[str], body.get("released_chunk_ids") or [])
    if released:
        lines.append(f"released: {', '.join(released)}")
    cli.show_lines(body, *lines)
