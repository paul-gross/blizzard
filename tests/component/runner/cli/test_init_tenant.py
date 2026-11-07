"""``blizzard runner init`` naming its tenant, run against a real hub holding two tenants."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from click.testing import CliRunner, Result
from fastapi import FastAPI
from fastapi.testclient import TestClient

from blizzard.cli.main import blizzard
from blizzard.hub.api.auth_session import TENANT_HEADER as HUB_TENANT_HEADER
from blizzard.runner.cli.runtime import HostClients, InitCollaborators, InitHubClients
from blizzard.runner.hub.client import (
    HostAddRefusalReason,
    HostAddRefused,
    TokenIdentity,
)
from blizzard.runner.hub.internal.http_hub import (
    HttpHostHubClient,
    HttpHubClient,
    HttpHubRunnerAdmin,
)
from blizzard.runner.hub.internal.token_file import HubTokenFile
from blizzard.runner.hub.token_file import ENV_FILENAME
from tests.support.runner_init_fakes import FakeInitHub, FakeTokenIdentityReader, init_runner
from tests.support.runner_init_tenant import (
    _ALPHA,  # pyright: ignore[reportPrivateUsage]
    _BRAVO,  # pyright: ignore[reportPrivateUsage]
)
from tests.support.runner_join import DEFAULT_TOKEN_ENV
from tests.support.scaffolding import build_hub, default_tenant
from tests.support.session_store_fakes import InMemorySessionStore

_HUB = "http://hub.test:8421"

_HOST_TENANT_REQUIRED = HostAddRefused(
    "POST /api/hosts -> 409", reason=HostAddRefusalReason.TENANT_REQUIRED, detail="name the tenant"
)


def _init(hub: FakeInitHub, root: Path, *args: str) -> Result:
    return init_runner(hub).invoke(blizzard, ["runner", "init", str(root), "--hub", _HUB, *args])


def test_init_adds_the_runner_in_the_tenant_it_names_and_says_so(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    root = tmp_path / "runner"
    result = _init(fake_init_hub, root, "--tenant", "ten_bravo")
    assert result.exit_code == 0, result.output
    assert fake_init_hub.admin.named == ["ten_bravo"]
    assert "added runner runner-local (rn_init1)" in result.output
    assert "in tenant ten_bravo" in result.output


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


def test_init_reports_an_unknown_adopted_runner(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    fake_init_hub.admin.host_refusal = HostAddRefused(
        "POST /api/hosts -> 404", reason=HostAddRefusalReason.RUNNER_NOT_FOUND, detail="unknown runner rn_missing"
    )
    result = _init(fake_init_hub, tmp_path / "runner")
    assert result.exit_code != 0
    assert "unknown runner rn_missing" in result.output
    assert "exists in the selected tenant" in result.output
    assert "predates hosts" not in result.output


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


def test_the_init_command_a_stop_suggests_carries_the_tenant(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    root = tmp_path / "runner"
    assert _init(fake_init_hub, root, "--tenant", "ten_bravo").exit_code == 0
    fake_init_hub.admin.added.clear()  # the hub's data is reset; the runtime dir survives
    result = _init(fake_init_hub, root, "--tenant", "ten_bravo")
    assert result.exit_code != 0
    assert f"`blizzard runner init {root} --tenant ten_bravo --allow-readd`" in result.output


def _real_hub_clients(app: FastAPI) -> InitCollaborators:
    """``init``'s hub calls routed into ``app``, its lifespan never started — the identity read under
    the runner's token, the add and the listing under no credential, as on a hub without sign-in."""

    @contextmanager
    def clients(_hub_url: str, runner_token: str, operator_token: str | None) -> Generator[InitHubClients]:
        bearer = {"Authorization": f"Bearer {runner_token}"} if runner_token else {}
        yield (
            HttpHubClient(TestClient(app, headers=bearer)),
            HttpHubRunnerAdmin(TestClient(app), operator_token=operator_token),
        )

    @contextmanager
    def host_clients(_hub_url: str) -> Generator[HostClients]:
        yield lambda token: HttpHostHubClient(TestClient(app), token=token)

    return InitCollaborators(clients, host_clients, InMemorySessionStore())


def _runner_ids(client: TestClient, tenant: str) -> list[str]:
    listed = client.get("/api/runners", headers={HUB_TENANT_HEADER: tenant})
    assert listed.status_code == 200, listed.text
    return [r["runner_id"] for r in listed.json()["runners"]]


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
