"""How a hub verb names its tenant: ``--tenant`` sends ``X-Blizzard-Tenant`` and nothing else does; a person
who belongs to several tenants and names none, or names one they cannot enter, is told their tenants, ids beside
names, whatever the verb; hub-level verbs carry no ``--tenant``; and a CLI sign-in belongs to the person, never
to one tenant — against a real hub holding two."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from click.testing import Result

from blizzard.auth_core import Role
from blizzard.hub.api.auth_session import TENANT_HEADER
from blizzard.hub.auth.pkce import Pkce
from blizzard.hub.cli import hub as hub_group
from blizzard.hub.cli.command import AdminCommand, FleetCommand
from blizzard.hub.config import AUTH_MODE_OAUTH
from tests.support import HubHarness, build_hub, default_tenant, seed_membership, seed_runner, seed_session, seed_user
from tests.worker_http import Bound, bind_stubs

pytestmark = pytest.mark.component

_HUB_URL = "http://testserver"
_LOOPBACK_REDIRECT = "http://127.0.0.1:54321/callback"
_VERIFIER = "a-fixed-test-verifier-with-enough-entropy-1234567890"


def _cli_against(hub: HubHarness) -> list[dict[str, str]]:
    """Route the CLI's ``GET``s to ``hub``, bind the runner :func:`_runner_list` invokes through, and hand back the
    headers each request sent, in order."""
    sent: list[dict[str, str]] = []

    def get(url: str, *, timeout: float, **kwargs: Any) -> httpx.Response:
        sent.append(dict(kwargs.get("headers") or {}))
        return hub.client.get(url, **kwargs)

    _bound["cli"] = bind_stubs(get=get)
    return sent


_bound: dict[str, Bound] = {}
"""The collaborators the routing helpers bound, for the invokers below."""


def _runner_list(*args: str) -> Result:
    return _bound["cli"].runner.invoke(hub_group, ["runner", "list", "--json", "--hub-url", _HUB_URL, *args])


def test_a_verb_naming_no_tenant_sends_no_tenant_header(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    sent = _cli_against(hub)

    result = _runner_list()

    assert result.exit_code == 0, result.output
    assert sent and all(TENANT_HEADER not in headers for headers in sent)


def test_tenant_names_the_tenant_the_verb_acts_in(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    bravo = hub.add_tenant("bravo")
    seed_runner(bravo.services, "bravo-r1")
    sent = _cli_against(hub)

    in_bravo = _runner_list("--tenant", bravo.tenant)
    in_default = _runner_list("--tenant", default_tenant(hub.engine))

    assert in_bravo.exit_code == 0, in_bravo.output
    assert "bravo-r1" in in_bravo.output
    assert in_default.exit_code == 0, in_default.output
    assert "bravo-r1" not in in_default.output
    assert {headers[TENANT_HEADER] for headers in sent} == {bravo.tenant, default_tenant(hub.engine)}


def test_naming_no_tenant_among_several_lists_the_callers_tenants(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    bravo = hub.add_tenant("bravo")
    _cli_against(hub)

    result = _runner_list()

    assert result.exit_code != 0
    assert "--tenant <tenant_id>" in result.output
    assert f"{bravo.tenant}  bravo  (admin)" in result.output
    assert default_tenant(hub.engine) in result.output


def _cli_routed(hub: HubHarness) -> Bound:
    """Route every CLI request to ``hub``."""

    def forward(method: str, url: str, *, timeout: float, **kwargs: Any) -> httpx.Response:
        return hub.client.request(method.upper(), url, **kwargs)

    return bind_stubs(request=forward)


@pytest.mark.parametrize(
    ("args", "stdin"),
    [(["runner", "add", "r9"], None), (["secret", "set", "gh-foo"], "v"), (["runner", "list"], None)],
    ids=["runner-add", "secret-set", "runner-list"],
)
def test_a_tenant_the_caller_cannot_enter_is_named_as_such(tmp_path: Path, args: list[str], stdin: str | None) -> None:
    hub = build_hub(tmp_path)
    bravo = hub.add_tenant("bravo")
    bound = _cli_routed(hub)

    result = bound.runner.invoke(hub_group, [*args, "--tenant", "ten_01BOGUS", "--hub-url", _HUB_URL], input=stdin)

    assert result.exit_code != 0
    assert "no tenant ten_01BOGUS you can enter" in result.output
    assert f"{bravo.tenant}  bravo  (admin)" in result.output
    assert "predates" not in result.output and "unknown secret" not in result.output


def test_a_verbs_own_404_keeps_its_own_message(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    bravo = hub.add_tenant("bravo")
    bound = _cli_routed(hub)

    result = bound.runner.invoke(
        hub_group, ["secret", "set", "gh-foo", "--tenant", bravo.tenant, "--hub-url", _HUB_URL], input="v"
    )

    assert result.exit_code != 0
    assert "unknown secret gh-foo" in result.output


def test_only_a_tenant_verb_offers_tenant() -> None:
    def verb(cli: object) -> None: ...

    fleet = {param.name for param in FleetCommand("fleet-verb", callback=verb).params}
    admin = {param.name for param in AdminCommand("admin-verb", callback=verb).params}

    assert {"tenant", "as_json", "hub_url"} <= fleet
    assert "tenant" not in admin
    assert {"as_json", "hub_url"} <= admin


def test_a_cli_sign_in_belongs_to_the_person_and_no_tenant(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    bravo = hub.add_tenant("bravo")
    ada = seed_user(hub, username="ada", role=Role.CONTRIBUTOR, email="ada@example.com")
    seed_membership(hub.engine, bravo.tenant, ada.user_id, Role.CONTRIBUTOR)
    hub.client.cookies.set("bz_session", seed_session(hub, ada))
    authorize = hub.client.get(
        "/api/auth/authorize",
        params={
            "client": "cli",
            "redirect_uri": _LOOPBACK_REDIRECT,
            "state": "s",
            "code_challenge": Pkce(_VERIFIER).challenge,
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    code = parse_qs(urlparse(authorize.headers["location"]).query)["code"][0]
    hub.client.cookies.clear()
    exchange = hub.client.post(
        "/api/auth/cli/token", json={"code": code, "code_verifier": _VERIFIER, "redirect_uri": _LOOPBACK_REDIRECT}
    )
    bearer = {"Authorization": f"Bearer {exchange.json()['token']}"}

    me = hub.client.get("/api/me", headers=bearer).json()
    unnamed = hub.client.get("/api/chunks", headers=bearer)

    assert {t["tenant_id"] for t in me["tenants"]} == {default_tenant(hub.engine), bravo.tenant}
    assert me["role"] is None
    assert unnamed.status_code == 409
    assert unnamed.json()["detail"]["code"] == "tenant_required"
    for tenant in (default_tenant(hub.engine), bravo.tenant):
        assert hub.client.get("/api/chunks", headers={**bearer, TENANT_HEADER: tenant}).status_code == 200
