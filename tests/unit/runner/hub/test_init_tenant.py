"""``blizzard runner init`` names its tenant — the join's rules by value, the HTTP adapter's wire, and the CLI's
words: ``--tenant <tenant_id>`` adds the runner there, no flag takes the tenant the hub resolves for the operator, a
choice the hub leaves stops listing the tenants the operator may enter, and a held token whose runner serves another
tenant stops naming both."""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx2
import pytest

from blizzard.auth_core import Role
from blizzard.foundation.runner_tokens import RunnerTokenRefusalReason
from blizzard.hub.api.auth_session import TENANT_HEADER as HUB_TENANT_HEADER
from blizzard.hub.api.me import MeResponse, MeTenant
from blizzard.runner.hub.bootstrap import BootstrapStop, BootstrapStopped, HeldToken, RunnerBootstrap
from blizzard.runner.hub.client import (
    HubClientError,
    IssuedIdentity,
    TokenIdentity,
    TokenRefusal,
)
from blizzard.runner.hub.internal.http_hub import (
    TENANT_HEADER,
    HttpHubRunnerAdmin,
)
from blizzard.wire.user import CallerTenantsView, CallerTenantView
from tests.support.runner_init_fakes import FakeHubRunnerAdmin, FakeTokenIdentityReader
from tests.support.runner_init_tenant import (
    _ALPHA,  # pyright: ignore[reportPrivateUsage]
    _BRAVO,  # pyright: ignore[reportPrivateUsage]
)

_FROM_FILE = HeldToken("t", from_process_env=False)

_IN_ALPHA = TokenIdentity(runner_id="rn_1", runner_name="r", tenant_id="ten_alpha", tenant_name="alpha")


@dataclass
class _Log:
    """Every hub and store call one join makes, in order."""

    calls: list[str] = field(default_factory=list[str])
    stored: str | None = None

    def stored_runner_id(self) -> str | None:
        return self.stored

    def is_set_aside(self) -> bool:
        return False

    def sharing(self) -> tuple[str, ...]:
        return ()

    def set_aside(self) -> None:
        self.calls.append("set_aside")


@dataclass
class _LoggedIdentity(FakeTokenIdentityReader):
    log: _Log = field(default_factory=_Log)

    def identity(self) -> TokenIdentity | TokenRefusal:
        self.log.calls.append("identity")
        return super().identity()


@dataclass
class _LoggedAdmin(FakeHubRunnerAdmin):
    log: _Log = field(default_factory=_Log)

    def add_runner(
        self, name: str, *, tenant_id: str | None = None, projects: tuple[str, ...] | None = None
    ) -> IssuedIdentity:
        self.log.calls.append("add")
        return super().add_runner(name, tenant_id=tenant_id, projects=projects)


class _Tokens:
    def write(self, token: str) -> None:
        pass


def _bootstrap(log: _Log, answer: TokenIdentity | TokenRefusal, admin: _LoggedAdmin) -> RunnerBootstrap:
    return RunnerBootstrap(_LoggedIdentity(answer, log=log), admin, _Tokens(), log)


def test_a_held_token_whose_runner_serves_another_tenant_stops_before_anything_is_set_aside_or_added() -> None:
    log = _Log(stored="rn_1")
    admin = _LoggedAdmin(log=log)
    with pytest.raises(BootstrapStopped) as stopped:
        _bootstrap(log, _IN_ALPHA, admin).join("r", held=_FROM_FILE, allow_readd=True, tenant_id="ten_bravo")
    assert (stopped.value.stop, stopped.value.runner_id, stopped.value.held) == (
        BootstrapStop.ANOTHER_TENANT,
        "rn_1",
        _IN_ALPHA,
    )
    assert log.calls == ["identity"]
    assert admin.added == []


@pytest.mark.parametrize(
    "answer",
    [_IN_ALPHA, TokenIdentity(runner_id="rn_1", runner_name="r")],
    ids=["the-named-tenant", "a-hub-that-names-no-tenant"],
)
def test_a_held_token_in_the_named_tenant_or_from_a_hub_naming_none_is_kept(answer: TokenIdentity) -> None:
    log = _Log(stored="rn_1")
    joined = _bootstrap(log, answer, _LoggedAdmin(log=log)).join(
        "r", held=_FROM_FILE, allow_readd=False, tenant_id="ten_alpha"
    )
    assert (joined.runner_id, joined.added, joined.tenant_id) == ("rn_1", False, answer.tenant_id)
    assert log.calls == ["identity"]


def test_an_unknown_token_is_asked_about_first_then_the_runner_is_added_in_its_hosts_tenant() -> None:
    log = _Log(stored="rn_old")
    admin = _LoggedAdmin(log=log)
    joined = _bootstrap(log, TokenRefusal(reason=RunnerTokenRefusalReason.UNKNOWN), admin).join(
        "r", held=_FROM_FILE, allow_readd=True, tenant_id="ten_bravo"
    )
    assert log.calls == ["identity", "set_aside", "add"]
    assert (joined.added, joined.tenant_id) == (True, "ten_bravo")


def _admin(handler: httpx2.MockTransport, operator_token: str | None = None) -> HttpHubRunnerAdmin:
    return HttpHubRunnerAdmin(
        httpx2.Client(base_url="http://hub.test", transport=handler), operator_token=operator_token
    )


def test_the_runner_names_its_tenant_in_the_header_the_hub_reads() -> None:
    assert TENANT_HEADER == HUB_TENANT_HEADER


def test_the_runner_reads_its_tenants_from_the_answer_the_hub_gives_me() -> None:
    me = MeResponse(
        user_id="u",
        username="op",
        display_name="Op",
        role=None,
        permissions=[],
        tenants=[MeTenant(tenant_id="ten_alpha", name="alpha", role=Role.ADMIN, permissions=[])],
    )
    read = CallerTenantsView.model_validate(me.model_dump(mode="json"))
    assert read.tenants == [CallerTenantView(tenant_id="ten_alpha", name="alpha")]


def test_the_operators_tenants_are_read_from_me_under_their_session() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert (request.method, request.url.path) == ("GET", "/api/me")
        assert request.headers.get("Authorization") == "Bearer op-session"
        tenants: list[dict[str, object]] = [
            {"tenant_id": t.tenant_id, "name": t.name, "role": "admin", "permissions": []} for t in (_ALPHA, _BRAVO)
        ]
        return httpx2.Response(200, json={"user_id": "u", "username": "op", "tenants": tenants})

    assert _admin(httpx2.MockTransport(handler), "op-session").tenants() == [_ALPHA, _BRAVO]


@pytest.mark.parametrize("answer", [httpx2.Response(401, json={"detail": "not authenticated"}), httpx2.Response(200)])
def test_an_unread_tenant_listing_raises(answer: httpx2.Response) -> None:
    with pytest.raises(HubClientError):
        _admin(httpx2.MockTransport(lambda _request: answer)).tenants()
