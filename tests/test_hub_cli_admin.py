"""``blizzard hub admin list|grant|revoke`` — the hub's administrators, against a real hub with sign-in on — and the
hub-level verbs (tenant, admin, graph sync, egress, traces), which act on the hub as a whole and take no ``--tenant``."""

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


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    default_tenant(hub.engine)
    return hub


@pytest.fixture
def hal(hub: HubHarness) -> User:
    hal = seed_user(hub, username="hal", role=Role.PENDING, email="hal@example.com")
    seed_hub_admin(hub.engine, hal.user_id)
    return hal


@pytest.fixture
def cli(hub: HubHarness, hal: User) -> Cli:
    """Every call the CLI makes routed to ``hub``, signed in as the hub administrator ``hal``."""
    session = seed_session(hub, hal)

    def forward(method: str, url: str, *, timeout: float, headers: dict[str, str] | None = None, **kwargs: Any) -> Any:
        signed_in = {"Authorization": f"Bearer {session}", **(headers or {})}
        return hub.client.request(method, url, headers=signed_in, **kwargs)

    bound = bind_stubs(request=forward)
    return lambda *argv: bound.runner.invoke(hub_group, [*argv, "--hub-url", _HUB_URL])


def test_a_hub_administrator_lists_grants_and_revokes_another(hub: HubHarness, hal: User, cli: Cli) -> None:
    ada = seed_user(hub, username="ada", role=Role.ADMIN)

    granted = cli("admin", "grant", ada.user_id)
    listed = cli("admin", "list")
    revoked = cli("admin", "revoke", ada.user_id)

    assert granted.exit_code == 0, granted.output
    assert _said(granted) == [f"{ada.user_id} (ada) is a hub administrator"]
    assert listed.exit_code == 0, listed.output
    assert sorted(_said(listed)) == sorted(
        [f"{hal.user_id}  hal  email=hal@example.com", f"{ada.user_id}  ada  email=-"]
    )
    assert revoked.exit_code == 0, revoked.output
    assert _said(cli("admin", "list")) == [f"{hal.user_id}  hal  email=hal@example.com"]


def test_the_last_hub_administrator_and_an_unknown_user_are_refused(hal: User, cli: Cli) -> None:
    last = cli("admin", "revoke", hal.user_id)
    unknown = cli("admin", "grant", "usr_nobody")

    assert last.exit_code != 0
    assert "last hub administrator" in last.output
    assert unknown.exit_code != 0
    assert "no such user" in unknown.output
    assert _said(cli("admin", "list")) == [f"{hal.user_id}  hal  email=hal@example.com"]


def test_graph_sync_reconciles_every_open_tenant(hub: HubHarness, cli: Cli) -> None:
    bravo = hub.add_tenant("bravo")

    result = cli("graph", "sync")

    assert result.exit_code == 0, result.output
    headers = [line for line in _said(result) if not line.startswith("  ")]
    assert sorted(headers) == sorted([f"{default_tenant(hub.engine)}  default", f"{bravo.tenant}  bravo"])


@pytest.mark.parametrize(
    "argv",
    [
        ["tenant", "list"],
        ["tenant", "create", "acme"],
        ["admin", "list"],
        ["graph", "sync"],
        ["egress", "status"],
        ["traces", "status"],
    ],
)
def test_a_hub_level_verb_takes_no_tenant(argv: list[str]) -> None:
    result = CliRunner().invoke(hub_group, [*argv, "--tenant", "ten_a", "--hub-url", _HUB_URL])

    assert result.exit_code == 2
    assert "No such option '--tenant'" in result.output
