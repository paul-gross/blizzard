"""``blizzard hub member list|grant|revoke`` — a tenant's members by user id, against a real hub with sign-in on and
two tenants: a tenant admin manages their own tenant's members, a non-member answers like an unknown user, a hub
administrator grants anyone into any tenant named with ``--tenant``, and the hub's refusals reach the operator."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

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


def test_a_tenant_admin_lists_grants_and_revokes_their_tenants_members(hub: HubHarness) -> None:
    hub.add_tenant("bravo")
    ada = seed_user(hub, username="ada", role=Role.ADMIN)
    cara = seed_user(hub, username="cara", role=Role.GUEST, email="cara@example.com")
    cli = _cli_as(hub, ada)

    granted = cli("member", "grant", cara.user_id, "contributor")
    listed = cli("member", "list")
    revoked = cli("member", "revoke", cara.user_id)

    assert granted.exit_code == 0, granted.output
    assert _said(granted) == [f"{cara.user_id} (cara) is now contributor"]
    assert listed.exit_code == 0, listed.output
    assert f"{cara.user_id}  cara  contributor  email=cara@example.com" in listed.output
    assert f"{ada.user_id}  ada  admin" in listed.output
    assert revoked.exit_code == 0, revoked.output
    assert hub.hub_services.memberships.role_in(default_tenant(hub.engine), cara.user_id) is None


def test_a_user_who_is_no_member_answers_like_an_unknown_one(hub: HubHarness) -> None:
    ada = seed_user(hub, username="ada", role=Role.ADMIN)
    outsider = seed_user(hub, username="otto", role=Role.PENDING)
    cli = _cli_as(hub, ada)

    outside = cli("member", "grant", outsider.user_id, "guest")
    unknown = cli("member", "grant", "usr_nobody", "guest")

    assert outside.exit_code != 0 and unknown.exit_code != 0
    assert [line.replace(outsider.user_id, "<id>") for line in _said(outside)] == [
        line.replace("usr_nobody", "<id>") for line in _said(unknown)
    ]
    assert hub.hub_services.memberships.role_in(default_tenant(hub.engine), outsider.user_id) is None


def test_a_hub_administrator_grants_anyone_into_the_tenant_named(hub: HubHarness) -> None:
    bravo = hub.add_tenant("bravo")
    hal = seed_user(hub, username="hal", role=Role.PENDING)
    seed_hub_admin(hub.engine, hal.user_id)
    otto = seed_user(hub, username="otto", role=Role.PENDING)
    cli = _cli_as(hub, hal)

    granted = cli("member", "grant", otto.user_id, "admin", "--tenant", bravo.tenant)

    assert granted.exit_code == 0, granted.output
    assert hub.hub_services.memberships.role_in(bravo.tenant, otto.user_id) is Role.ADMIN
    assert hub.hub_services.memberships.role_in(default_tenant(hub.engine), otto.user_id) is None


def test_the_hubs_refusals_reach_the_operator(hub: HubHarness) -> None:
    ada = seed_user(hub, username="ada", role=Role.ADMIN)
    cli = _cli_as(hub, ada)

    own = cli("member", "revoke", ada.user_id)

    assert own.exit_code != 0
    assert "Error:" in own.output
    assert hub.hub_services.memberships.role_in(default_tenant(hub.engine), ada.user_id) is Role.ADMIN


@pytest.mark.parametrize("role", ["superuser", "pending"])
def test_grant_offers_only_the_membership_roles(role: str) -> None:
    result = CliRunner().invoke(hub_group, ["member", "grant", "usr_a", role, "--hub-url", _HUB_URL])

    assert result.exit_code == 2
    assert "'guest', 'contributor', 'admin'" in result.output
