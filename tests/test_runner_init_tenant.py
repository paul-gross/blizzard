"""``blizzard runner init`` names its tenant — ``--tenant <tenant_id>`` adds the runner there, no flag
takes the tenant the hub resolves for the operator, a choice the hub leaves stops listing the
tenants the operator may enter, and a held token whose runner serves another tenant stops naming
both. The join's rules by value, the HTTP adapter's wire, the CLI's words, and one run against a
real hub holding two tenants."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner, Result
from fastapi import FastAPI
from fastapi.testclient import TestClient

from blizzard.auth_core import Role
from blizzard.cli.main import blizzard
from blizzard.foundation.runner_tokens import RunnerTokenRefusalReason
from blizzard.hub.api.auth_session import TENANT_HEADER as HUB_TENANT_HEADER
from blizzard.hub.api.me import MeResponse, MeTenant
from blizzard.runner.cli.runtime import HostClients, InitCollaborators, InitHubClients
from blizzard.runner.hub.bootstrap import BootstrapStop, BootstrapStopped, HeldToken, RunnerBootstrap
from blizzard.runner.hub.client import (
    HostAddRefusalReason,
    HostAddRefused,
    HubClientError,
    IssuedIdentity,
    OperatorTenant,
    TokenIdentity,
    TokenRefusal,
)
from blizzard.runner.hub.internal.http_hub import (
    TENANT_HEADER,
    HttpHostHubClient,
    HttpHubClient,
    HttpHubRunnerAdmin,
)
from blizzard.runner.hub.internal.token_file import HubTokenFile
from blizzard.runner.hub.token_file import ENV_FILENAME
from blizzard.wire.user import CallerTenantsView, CallerTenantView
from tests.runner_init_fakes import FakeHubRunnerAdmin, FakeInitHub, FakeTokenIdentityReader, init_runner
from tests.runner_join import DEFAULT_TOKEN_ENV
from tests.support import build_hub, default_tenant

_HUB = "http://hub.test:8421"
_FROM_FILE = HeldToken("t", from_process_env=False)
_ALPHA = OperatorTenant(tenant_id="ten_alpha", name="alpha")
_BRAVO = OperatorTenant(tenant_id="ten_bravo", name="bravo")
_IN_ALPHA = TokenIdentity(runner_id="rn_1", runner_name="r", tenant_id="ten_alpha", tenant_name="alpha")
_HOST_TENANT_REQUIRED = HostAddRefused(
    "POST /api/hosts -> 409", reason=HostAddRefusalReason.TENANT_REQUIRED, detail="name the tenant"
)


# --- the join, by value ---------------------------------------------------------------------------


@dataclass
class _Log:
    """Every hub and store call one join makes, in order."""

    calls: list[str] = field(default_factory=list)
    stored: str | None = None

    def stored_runner_id(self) -> str | None:
        return self.stored

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


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
def test_an_unknown_token_is_asked_about_first_then_the_runner_is_added_in_its_hosts_tenant() -> None:
    log = _Log(stored="rn_old")
    admin = _LoggedAdmin(log=log)
    joined = _bootstrap(log, TokenRefusal(reason=RunnerTokenRefusalReason.UNKNOWN), admin).join(
        "r", held=_FROM_FILE, allow_readd=True, tenant_id="ten_bravo"
    )
    assert log.calls == ["identity", "set_aside", "add"]
    assert (joined.added, joined.tenant_id) == (True, "ten_bravo")


# --- the wire ---------------------------------------------------------------------------------------


def _admin(handler: httpx.MockTransport, operator_token: str | None = None) -> HttpHubRunnerAdmin:
    return HttpHubRunnerAdmin(
        httpx.Client(base_url="http://hub.test", transport=handler), operator_token=operator_token
    )


@pytest.mark.unit
def test_the_runner_names_its_tenant_in_the_header_the_hub_reads() -> None:
    assert TENANT_HEADER == HUB_TENANT_HEADER


@pytest.mark.unit
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


@pytest.mark.unit
def test_the_operators_tenants_are_read_from_me_under_their_session() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert (request.method, request.url.path) == ("GET", "/api/me")
        assert request.headers.get("Authorization") == "Bearer op-session"
        tenants = [
            {"tenant_id": t.tenant_id, "name": t.name, "role": "admin", "permissions": []} for t in (_ALPHA, _BRAVO)
        ]
        return httpx.Response(200, json={"user_id": "u", "username": "op", "tenants": tenants})

    assert _admin(httpx.MockTransport(handler), "op-session").tenants() == [_ALPHA, _BRAVO]


@pytest.mark.unit
@pytest.mark.parametrize("answer", [httpx.Response(401, json={"detail": "not authenticated"}), httpx.Response(200)])
def test_an_unread_tenant_listing_raises(answer: httpx.Response) -> None:
    with pytest.raises(HubClientError):
        _admin(httpx.MockTransport(lambda _request: answer)).tenants()


# --- the command's words ----------------------------------------------------------------------------


def _init(hub: FakeInitHub, root: Path, *args: str) -> Result:
    return init_runner(hub).invoke(blizzard, ["runner", "init", str(root), "--hub", _HUB, *args])


@pytest.mark.component
def test_init_adds_the_runner_in_the_tenant_it_names_and_says_so(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    root = tmp_path / "runner"
    result = _init(fake_init_hub, root, "--tenant", "ten_bravo")
    assert result.exit_code == 0, result.output
    assert fake_init_hub.admin.named == ["ten_bravo"]
    assert "added runner runner-local (rn_init1)" in result.output
    assert "in tenant ten_bravo" in result.output


@pytest.mark.component
def test_init_left_a_choice_stops_listing_ids_beside_names_and_how_to_name_one(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = tmp_path / "runner"
    fake_init_hub.admin.host_refusal = _HOST_TENANT_REQUIRED
    fake_init_hub.admin.tenants_answer = [_ALPHA, _BRAVO]
    result = _init(fake_init_hub, root)
    assert result.exit_code != 0
    assert "nothing was added" in result.output
    assert f"blizzard runner init {root} --tenant <tenant_id>" in result.output
    assert "  ten_alpha  alpha" in result.output
    assert "  ten_bravo  bravo" in result.output
    assert not HubTokenFile.of(root, DEFAULT_TOKEN_ENV).held()


@pytest.mark.component
def test_init_naming_a_tenant_it_may_not_enter_stops_listing_the_ones_it_may(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    fake_init_hub.admin.host_refusal = HostAddRefused(
        "POST /api/hosts -> 404", reason=HostAddRefusalReason.NO_SUCH_TENANT, detail="no such tenant"
    )
    fake_init_hub.admin.tenants_answer = [_ALPHA]
    result = _init(fake_init_hub, tmp_path / "runner", "--tenant", "alpha")
    assert result.exit_code != 0
    assert "no tenant alpha you may enter" in result.output
    assert "never its name" in result.output
    assert "  ten_alpha  alpha" in result.output


@pytest.mark.component
def test_init_whose_held_token_serves_another_tenant_stops_naming_both_and_keeps_everything(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = tmp_path / "runner"
    assert _init(fake_init_hub, root, "--tenant", "ten_bravo").exit_code == 0
    env_bytes = (root / ENV_FILENAME).read_bytes()
    fake_init_hub.identity = FakeTokenIdentityReader(
        TokenIdentity(runner_id="rn_init1", runner_name="runner-local", tenant_id="ten_alpha", tenant_name="alpha")
    )
    result = _init(fake_init_hub, root, "--tenant", "ten_bravo", "--allow-readd")
    assert result.exit_code != 0
    assert "runner rn_init1, which serves tenant alpha (ten_alpha)" in result.output
    assert "--tenant names tenant ten_bravo" in result.output
    assert f"`blizzard runner init {root}` to keep this runner" in result.output
    assert "blizzard runner init <new-directory> --tenant ten_bravo" in result.output
    assert len(fake_init_hub.admin.added) == 1
    assert (root / ENV_FILENAME).read_bytes() == env_bytes


@pytest.mark.component
def test_the_init_command_a_stop_suggests_carries_the_tenant(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    root = tmp_path / "runner"
    assert _init(fake_init_hub, root, "--tenant", "ten_bravo").exit_code == 0
    fake_init_hub.admin.added.clear()  # the hub's data is reset; the runtime dir survives
    result = _init(fake_init_hub, root, "--tenant", "ten_bravo")
    assert result.exit_code != 0
    assert f"`blizzard runner init {root} --tenant ten_bravo --allow-readd`" in result.output


# --- against a hub holding two tenants ------------------------------------------------------------


def _real_hub_clients(app: FastAPI) -> InitCollaborators:
    """``init``'s hub calls routed into ``app``, its lifespan never started — the identity read under
    the runner's token, the add and the listing under no credential, as on a hub without sign-in."""

    @contextmanager
    def clients(_hub_url: str, runner_token: str, operator_token: str | None) -> Iterator[InitHubClients]:
        bearer = {"Authorization": f"Bearer {runner_token}"} if runner_token else {}
        yield (
            HttpHubClient(TestClient(app, headers=bearer)),
            HttpHubRunnerAdmin(TestClient(app), operator_token=operator_token),
        )

    @contextmanager
    def host_clients(_hub_url: str) -> Iterator[HostClients]:
        yield lambda token: HttpHostHubClient(TestClient(app), token=token)

    return InitCollaborators(clients, host_clients)


def _runner_ids(client: TestClient, tenant: str) -> list[str]:
    listed = client.get("/api/runners", headers={HUB_TENANT_HEADER: tenant})
    assert listed.status_code == 200, listed.text
    return [r["runner_id"] for r in listed.json()["runners"]]


@pytest.mark.component
def test_init_against_a_hub_with_two_tenants_needs_the_tenant_named_and_never_moves_a_runner(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    first = default_tenant(hub.engine)
    bravo = hub.add_tenant("bravo").tenant
    assert hub.app is not None
    collaborators = _real_hub_clients(hub.app)
    root = tmp_path / "runner"
    invoke = CliRunner().invoke

    def init(*args: str) -> Result:
        return invoke(blizzard, ["runner", "init", str(root), "--hub", _HUB, *args], obj=collaborators)

    unnamed = init()
    assert unnamed.exit_code != 0
    assert f"  {first}  " in unnamed.output
    assert f"  {bravo}  bravo" in unnamed.output
    assert not HubTokenFile.of(root, DEFAULT_TOKEN_ENV).held()
    observer = TestClient(hub.app)
    assert _runner_ids(observer, first) == [] and _runner_ids(observer, bravo) == []

    added = init("--tenant", bravo)
    assert added.exit_code == 0, added.output
    token = HubTokenFile.of(root, DEFAULT_TOKEN_ENV).held()
    identity = HttpHubClient(TestClient(hub.app, headers={"Authorization": f"Bearer {token}"})).identity()
    assert isinstance(identity, TokenIdentity)
    assert (identity.tenant_id, identity.tenant_name) == (bravo, "bravo")
    assert _runner_ids(observer, bravo) == [identity.runner_id]

    moved = init("--tenant", first)
    assert moved.exit_code != 0
    assert f"which serves tenant {bravo}" in moved.output
    assert f"--tenant names tenant {first}" in moved.output
    assert _runner_ids(observer, first) == []

    kept = init("--tenant", bravo)
    assert kept.exit_code == 0, kept.output
    assert f"in tenant bravo ({bravo}) keeps its token" in kept.output
    assert HubTokenFile.of(root, DEFAULT_TOKEN_ENV).held() == token
    assert _runner_ids(observer, bravo) == [identity.runner_id]
