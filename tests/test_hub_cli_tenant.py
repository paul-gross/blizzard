"""``blizzard hub tenant create|list|rename|delete`` — a hub administrator's verbs, against a real hub with sign-in on:
create names its admin and records the CLI as the door of the tenant's default project, list shows a deletion
underway, rename keeps the id, and delete asks first and answers each of the hub's outcomes — removed, underway,
already deleted, and the only tenant refused."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from click.testing import CliRunner, Result

from blizzard.auth_core import Role
from blizzard.hub.cli import hub as hub_group
from blizzard.hub.config import AUTH_MODE_OAUTH
from blizzard.hub.store import schema
from blizzard.hub.tenancy import TenantId
from tests.support import HubHarness, build_hub, default_tenant, seed_hub_admin, seed_session, seed_user

_HUB_URL = "http://testserver"

Cli = Callable[..., Result]


def _said(result: Result) -> list[str]:
    """The CLI's own lines, without the hosted hub's log lines that share the captured stream."""
    return [line for line in result.output.splitlines() if '"event": ' not in line]


def _signed_in_cli(hub: HubHarness, session: str, monkeypatch: pytest.MonkeyPatch) -> Cli:
    """Route every call the CLI makes to ``hub``, signed in with ``session``, and hand back an invoker."""

    def routed(method: str) -> Callable[..., Any]:
        def call(url: str, *, timeout: float, headers: dict[str, str] | None = None, **kwargs: Any) -> Any:
            return hub.client.request(
                method, url, headers={"Authorization": f"Bearer {session}", **(headers or {})}, **kwargs
            )

        return call

    for method in ("get", "post", "put", "patch", "delete"):
        monkeypatch.setattr(httpx, method, routed(method))

    def invoke(*argv: str, stdin: str | None = None) -> Result:
        return CliRunner().invoke(hub_group, [*argv, "--hub-url", _HUB_URL], input=stdin)

    return invoke


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    default_tenant(hub.engine)
    return hub


@pytest.fixture
def cli(hub: HubHarness, monkeypatch: pytest.MonkeyPatch) -> Cli:
    hal = seed_user(hub, username="hal", role=Role.PENDING)
    seed_hub_admin(hub.engine, hal.user_id)
    return _signed_in_cli(hub, seed_session(hub, hal), monkeypatch)


def _created(result: Result) -> str:
    assert result.exit_code == 0, result.output
    return _said(result)[0].split()[2]


@pytest.mark.component
def test_create_names_its_admin_and_rename_keeps_the_id(hub: HubHarness, cli: Cli) -> None:
    ada = seed_user(hub, username="ada", role=Role.PENDING)

    tenant = _created(cli("tenant", "create", "acme", "--admin", ada.user_id))
    renamed = cli("tenant", "rename", tenant, "acme two")
    listed = cli("tenant", "list")

    assert renamed.exit_code == 0, renamed.output
    assert _said(renamed) == [f"tenant {tenant} is now named acme two"]
    assert listed.exit_code == 0, listed.output
    assert f"{tenant}  acme two  open" in listed.output
    assert f"{default_tenant(hub.engine)}  default  open" in listed.output
    assert hub.hub_services.memberships.role_in(TenantId(tenant), ada.user_id) is Role.ADMIN


@pytest.mark.component
def test_a_created_tenants_default_project_records_the_cli_as_its_door(hub: HubHarness, cli: Cli) -> None:
    tenant = TenantId(_created(cli("tenant", "create", "acme")))

    [project] = hub.open_services(tenant).projects.list_all()
    c = schema.config_changes.c
    with hub.engine.connect() as conn:
        doors = conn.execute(
            sa.select(c.door).where(
                c.tenant_id == tenant, c.record_kind == "project", c.record_key == project.project_id
            )
        ).scalars()
        assert list(doors) == ["cli"]


@pytest.mark.component
def test_create_with_an_unknown_admin_creates_nothing(hub: HubHarness, cli: Cli) -> None:
    result = cli("tenant", "create", "acme", "--admin", "usr_nobody")

    assert result.exit_code != 0
    assert "no such user" in result.output
    assert "acme" not in cli("tenant", "list").output


@pytest.mark.component
def test_rename_of_an_unknown_tenant_says_so(cli: Cli) -> None:
    result = cli("tenant", "rename", "ten_nobody", "acme")

    assert result.exit_code != 0
    assert "no such tenant" in result.output


@pytest.mark.component
def test_delete_asks_first_and_a_no_deletes_nothing(cli: Cli) -> None:
    tenant = _created(cli("tenant", "create", "acme"))

    declined = cli("tenant", "delete", tenant, stdin="n\n")

    assert declined.exit_code != 0
    assert f"{tenant}  acme  open" in cli("tenant", "list").output


@pytest.mark.component
def test_delete_removes_the_tenant_and_a_retry_learns_it_is_gone(cli: Cli) -> None:
    tenant = _created(cli("tenant", "create", "acme"))

    deleted = cli("tenant", "delete", tenant, "--yes")
    again = cli("tenant", "delete", tenant, "--yes", "--json")

    assert deleted.exit_code == 0, deleted.output
    assert _said(deleted)[0].startswith(f"deleted tenant {tenant} — ")
    assert _said(deleted)[0].endswith(" rows removed")
    assert again.exit_code == 0, again.output
    assert json.loads(_said(again)[0]) == {"tenant_id": tenant, "state": "deleted", "rows_removed": None}
    assert tenant not in cli("tenant", "list").output


@pytest.mark.component
def test_delete_refuses_the_hubs_only_open_tenant_and_an_unknown_one(hub: HubHarness, cli: Cli) -> None:
    only = cli("tenant", "delete", default_tenant(hub.engine), "--yes")
    unknown = cli("tenant", "delete", "ten_nobody", "--yes")

    assert only.exit_code != 0
    assert "the hub's only open tenant cannot be deleted" in only.output
    assert unknown.exit_code != 0
    assert "no such tenant" in unknown.output


@pytest.mark.unit
def test_a_deletion_left_underway_says_the_hub_finishes_it(monkeypatch: pytest.MonkeyPatch) -> None:
    def delete(url: str, *, timeout: float, headers: dict[str, str] | None = None) -> httpx.Response:
        body = {"tenant_id": "ten_a", "state": "deleting", "rows_removed": None}
        return httpx.Response(202, json=body, request=httpx.Request("DELETE", url))

    monkeypatch.setattr(httpx, "delete", delete)

    result = CliRunner().invoke(hub_group, ["tenant", "delete", "ten_a", "--yes", "--hub-url", _HUB_URL])

    assert result.exit_code == 0, result.output
    assert "deletion is underway" in result.output
    assert "tenant list" in result.output


@pytest.mark.unit
def test_list_shows_a_deletion_underway_with_when_it_began(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {"tenant_id": "ten_a", "name": "acme", "created_at": "t0", "created_by": "hal", "state": "open"},
        {
            "tenant_id": "ten_b",
            "name": "bravo",
            "created_at": "t1",
            "created_by": "hal",
            "state": "deleting",
            "closed_at": "t2",
        },
    ]

    def get(url: str, *, timeout: float, headers: dict[str, str] | None = None) -> httpx.Response:
        return httpx.Response(200, json=rows, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)

    result = CliRunner().invoke(hub_group, ["tenant", "list", "--hub-url", _HUB_URL])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "ten_a  acme  open  created_at=t0  by hal",
        "ten_b  bravo  deleting since t2",
    ]
