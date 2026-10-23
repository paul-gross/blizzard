"""``blizzard hub invite create|list|revoke`` — a tenant's invitations, against a real hub with sign-in on: create prints
the link once and never again, list shows each invitation's email, role, issuer and state without it, revoke answers
the hub's refusals, and the hub administrator invites into a tenant named with ``--tenant``."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2
import pytest
from click.testing import CliRunner, Result

from blizzard.auth_core import Role
from blizzard.hub.auth.models import User
from blizzard.hub.cli import hub as hub_group
from blizzard.hub.config import AUTH_MODE_OAUTH
from tests.support import HubHarness, build_hub, default_tenant, seed_hub_admin, seed_session, seed_user
from tests.worker_http import bind_stubs

pytestmark = pytest.mark.component

_HUB_URL = "http://testserver"

Cli = Callable[..., Result]


def _said(result: Result) -> list[str]:
    """The CLI's own lines, without the hosted hub's log lines that share the captured stream."""
    return [line for line in result.output.splitlines() if '"event": ' not in line]


def _cli_as(hub: HubHarness, user: User) -> Cli:
    """Route every call the CLI makes to ``hub``, signed in as ``user``, and hand back an invoker."""
    session = seed_session(hub, user)

    def forward(method: str, url: str, *, timeout: float, headers: dict[str, str] | None = None, **kwargs: Any) -> Any:
        signed_in = {"Authorization": f"Bearer {session}", **(headers or {})}
        return hub.client.request(method, url, headers=signed_in, **kwargs)

    bound = bind_stubs(request=forward)
    return lambda *argv: bound.runner.invoke(hub_group, [*argv, "--hub-url", _HUB_URL])


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    default_tenant(hub.engine)
    return hub


@pytest.fixture
def ada(hub: HubHarness) -> User:
    return seed_user(hub, username="ada", role=Role.ADMIN)


def _link_token(result: Result) -> str:
    assert result.exit_code == 0, result.output
    links = [line for line in _said(result) if line.startswith(f"{_HUB_URL}/invite/")]
    assert len(links) == 1, result.output
    return links[0].removeprefix(f"{_HUB_URL}/invite/")


def test_create_prints_the_link_once_and_list_never_shows_it(hub: HubHarness, ada: User) -> None:
    cli = _cli_as(hub, ada)

    token = _link_token(cli("invite", "create", "--email", "New@Example.com", "--role", "contributor"))
    listed = cli("invite", "list")
    listed_json = cli("invite", "list", "--json")

    assert len(token) >= 22
    assert listed.exit_code == 0, listed.output
    [row] = _said(listed)
    assert row.split()[1:5] == ["new@example.com", "contributor", "live", "issued"]
    assert f"issued by {ada.user_id}" in row
    assert token not in listed.output
    assert listed_json.exit_code == 0, listed_json.output
    assert token not in listed_json.output
    assert [i["state"] for i in json.loads(_said(listed_json)[0])["invitations"]] == ["live"]


def test_revoke_revokes_once_and_refuses_again(hub: HubHarness, ada: User) -> None:
    cli = _cli_as(hub, ada)
    cli("invite", "create", "--email", "new@example.com", "--role", "guest")
    [invitation_id] = [line.split()[0] for line in _said(cli("invite", "list"))]

    revoked = cli("invite", "revoke", invitation_id)
    again = cli("invite", "revoke", invitation_id)
    unknown = cli("invite", "revoke", "inv_nobody")

    assert revoked.exit_code == 0, revoked.output
    assert _said(revoked) == [f"revoked invitation {invitation_id} for new@example.com"]
    assert "revoked" in _said(cli("invite", "list"))[0].split()
    assert again.exit_code != 0
    assert unknown.exit_code != 0


def test_the_hub_administrator_invites_into_the_tenant_named(hub: HubHarness) -> None:
    bravo = hub.add_tenant("bravo")
    hal = seed_user(hub, username="hal", role=Role.PENDING)
    seed_hub_admin(hub.engine, hal.user_id)
    cli = _cli_as(hub, hal)

    _link_token(cli("invite", "create", "--email", "first@example.com", "--role", "admin", "--tenant", bravo.tenant))

    assert [i.invitation.email for i in bravo.services.invitations.list()] == ["first@example.com"]
    assert hub.open_services(default_tenant(hub.engine)).invitations.list() == []


@pytest.mark.parametrize(
    "argv",
    [
        ["--email", "a@example.com", "--role", "superuser"],
        ["--email", "a@example.com", "--role", "guest", "--expires", "0"],
        ["--email", "a@example.com", "--role", "guest", "--expires", "31"],
    ],
)
def test_create_refuses_a_role_or_lifetime_the_hub_would(argv: list[str]) -> None:
    result = CliRunner().invoke(hub_group, ["invite", "create", *argv, "--hub-url", _HUB_URL])

    assert result.exit_code == 2, result.output


def test_list_names_who_accepted_an_invitation() -> None:
    row = {
        "invitation_id": "inv_a",
        "email": "a@example.com",
        "role": "guest",
        "created_by": "usr_ada",
        "created_at": "t0",
        "expires_at": "t1",
        "state": "accepted",
        "accepted_by": "usr_bea",
    }

    def get(url: str, *, timeout: float, headers: dict[str, str] | None = None) -> httpx2.Response:
        return httpx2.Response(200, json={"invitations": [row]}, request=httpx2.Request("GET", url))

    bound = bind_stubs(get=get)

    result = bound.runner.invoke(hub_group, ["invite", "list", "--hub-url", _HUB_URL])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "inv_a  a@example.com  guest  accepted by usr_bea  issued by usr_ada  expires_at=t1"
    ]
