"""``/api/host/workspaces`` and ``/api/host/subscriptions`` as records over a real seeded host: each kind's create,
list, get, edit, retire and enable, a create or edit refused as the host file would be at boot, the edit's ``If-Match``
conflict, an idempotent retire and enable writing nothing, and every write gated on a superuser session while the
workspace prompt's stays on the human lane."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient

from blizzard.auth_core import Role
from blizzard.foundation.clock import SystemClock
from blizzard.runner.app import HostedApp, build_hosted_app
from blizzard.runner.auth.session import CookieNames, RunnerSession, SessionCookie
from blizzard.runner.cli.runtime import seed_host, seed_runners
from blizzard.runner.composition import HostProcess, build_host_process
from blizzard.runner.config import DEFAULT_WORKSPACE_ID, RunnerConfig
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.store.internal.identity_store import RunnerIdentityStore
from blizzard.runner.subscriptions.subscription_sampler import PROVIDER_ANTHROPIC
from tests.support.config_inputs import create_runner_app, load_host_config
from tests.support.host_files import TEST_RUNNER_ID, one_workspace, write_minimal_host
from tests.support.runner_fakes import make_store

_WORKSPACES = "/api/host/workspaces"
_SUBSCRIPTIONS = "/api/host/subscriptions"
_CHANGES = "/api/host/settings/changes"


@pytest.fixture
def hosted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[HostedApp]:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    url = f"sqlite:///{tmp_path / 'runner.db'}"
    write_minimal_host(tmp_path, top=f'db_url = "{url}"', workspace='prompt = "SEEDED"')
    store = make_store(url, TEST_RUNNER_ID)
    config = load_host_config(tmp_path)
    RunnerIdentityStore(store.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, config.runners[0].name, datetime.now(UTC))
    )
    config = seed_host(config, {}, clock=SystemClock())
    ready, halted = seed_runners(config, clock=SystemClock())
    assert not halted
    host: HostProcess = build_host_process(config, ready)
    app = build_hosted_app(host.runners[0].config, host)
    try:
        yield app
    finally:
        app.close()
        host.close()


def _ok(response: httpx2.Response) -> Any:
    assert response.status_code == 200, response.text
    return response.json()


def _problem(response: httpx2.Response, status: int, code: str) -> Any:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["code"] == code, body
    return body


def _changes(client: TestClient, kind: str, key: str) -> list[tuple[int, str, str]]:
    rows: list[dict[str, Any]] = _ok(client.get(f"{_CHANGES}?record_kind={kind}&record_key={key}"))["changes"]
    return [(row["revision"], row["op"], row["door"]) for row in rows]


def test_a_workspace_round_trips_every_verb(hosted: HostedApp, tmp_path: Path) -> None:
    with TestClient(hosted.app) as client:
        seeded = _ok(client.get(_WORKSPACES))["items"]
        assert seeded == [
            {
                "workspace_id": "workspace-local",
                "max_environments": 10,
                "revision": 1,
                "state": "active",
                "settings": {"prompt": "SEEDED"},
            }
        ]
        created = _ok(
            client.post(
                _WORKSPACES,
                json={"id": "ws-b", "provider": "basic", "root": str(tmp_path / "b"), "max_environments": 2},
            )
        )
        assert (created["revision"], created["state"], created["max_environments"]) == (1, "active", 2)
        assert _ok(client.get(f"{_WORKSPACES}/ws-b")) == created
        _problem(client.post(_WORKSPACES, json={"id": "ws-b"}), 409, "workspace_exists")

        edited = _ok(client.patch(f"{_WORKSPACES}/ws-b", headers={"If-Match": "1"}, json={"max_environments": 4}))
        assert (edited["revision"], edited["settings"]["max_environments"]) == (2, 4)
        stale = client.patch(f"{_WORKSPACES}/ws-b", headers={"If-Match": "1"}, json={"max_environments": 5})
        assert _problem(stale, 409, "revision_conflict")["current"] == 2
        _problem(client.patch(f"{_WORKSPACES}/ws-b", json={"max_environments": 5}), 409, "revision_conflict")
        # An explicit null returns a key to its default.
        reset = _ok(client.patch(f"{_WORKSPACES}/ws-b", headers={"If-Match": "2"}, json={"max_environments": None}))
        assert (reset["revision"], reset["max_environments"], "max_environments" in reset["settings"]) == (3, 10, False)
        assert client.patch(f"{_WORKSPACES}/ws-b", headers={"If-Match": "3"}, json={"id": "x"}).status_code == 422

        retired = _ok(client.post(f"{_WORKSPACES}/ws-b/retire"))
        assert (retired["revision"], retired["state"]) == (4, "retired")
        assert _ok(client.post(f"{_WORKSPACES}/ws-b/retire"))["revision"] == 4
        assert [i["workspace_id"] for i in _ok(client.get(_WORKSPACES))["items"]] == ["workspace-local"]
        listed = _ok(client.get(f"{_WORKSPACES}?retired=true"))["items"]
        assert [(i["workspace_id"], i["state"]) for i in listed] == [
            ("workspace-local", "active"),
            ("ws-b", "retired"),
        ]
        # An edit of a retired record is legal and leaves it retired.
        still = _ok(client.patch(f"{_WORKSPACES}/ws-b", headers={"If-Match": "4"}, json={"base_branch": "trunk"}))
        assert (still["revision"], still["state"]) == (5, "retired")
        enabled = _ok(client.post(f"{_WORKSPACES}/ws-b/enable"))
        assert (enabled["revision"], enabled["state"]) == (6, "active")
        assert _ok(client.post(f"{_WORKSPACES}/ws-b/enable"))["revision"] == 6

        assert _changes(client, "workspace", "ws-b") == [
            (6, "enable", "api"),
            (5, "edit", "api"),
            (4, "retire", "api"),
            (3, "edit", "api"),
            (2, "edit", "api"),
            (1, "create", "api"),
        ]
        for method, path in [
            ("get", f"{_WORKSPACES}/nope"),
            ("patch", f"{_WORKSPACES}/nope"),
            ("post", f"{_WORKSPACES}/nope/retire"),
            ("post", f"{_WORKSPACES}/nope/enable"),
        ]:
            _problem(client.request(method, path, headers={"If-Match": "1"}, json={}), 404, "workspace_unknown")


def test_a_workspace_the_host_file_would_refuse_is_refused_whole(hosted: HostedApp) -> None:
    with TestClient(hosted.app) as client:
        refused = _problem(client.post(_WORKSPACES, json={"id": "ws-x", "provider": "nope"}), 422, "settings_invalid")
        assert [error["key"] for error in refused["errors"]] == ["provider"]
        below = client.patch(f"{_WORKSPACES}/workspace-local", headers={"If-Match": "1"}, json={"max_environments": 0})
        assert [error["key"] for error in _problem(below, 422, "settings_invalid")["errors"]] == ["max_environments"]
        assert client.post(_WORKSPACES, json={"id": "ws-x", "colour": "blue"}).status_code == 422
        assert _ok(client.get(f"{_WORKSPACES}?retired=true"))["items"][0]["revision"] == 1
        assert [i["workspace_id"] for i in _ok(client.get(f"{_WORKSPACES}?retired=true"))["items"]] == [
            "workspace-local"
        ]


def test_a_subscription_round_trips_every_verb_and_keeps_its_diagnostics(hosted: HostedApp) -> None:
    with TestClient(hosted.app) as client:
        assert _ok(client.get(_SUBSCRIPTIONS))["items"] == []
        created = _ok(client.post(_SUBSCRIPTIONS, json={"slug": "sub-a", "name": "A", "provider": PROVIDER_ANTHROPIC}))
        assert (created["slug"], created["name"], created["provider"], created["revision"]) == (
            "sub-a",
            "A",
            PROVIDER_ANTHROPIC,
            1,
        )
        assert created["spenders"] == [] and created["sampled_at"] is None
        assert _ok(client.get(f"{_SUBSCRIPTIONS}/sub-a")) == created
        _problem(
            client.post(_SUBSCRIPTIONS, json={"slug": "sub-a", "name": "A", "provider": PROVIDER_ANTHROPIC}),
            409,
            "subscription_exists",
        )
        edited = _ok(client.patch(f"{_SUBSCRIPTIONS}/sub-a", headers={"If-Match": "1"}, json={"name": "Renamed"}))
        assert (edited["name"], edited["revision"]) == ("Renamed", 2)
        _problem(
            client.patch(f"{_SUBSCRIPTIONS}/sub-a", headers={"If-Match": "1"}, json={"name": "Again"}),
            409,
            "revision_conflict",
        )
        # The provider is immutable, and the required name refuses a null.
        immutable = client.patch(f"{_SUBSCRIPTIONS}/sub-a", headers={"If-Match": "2"}, json={"provider": "x"})
        assert immutable.status_code == 422
        _problem(
            client.patch(f"{_SUBSCRIPTIONS}/sub-a", headers={"If-Match": "2"}, json={"name": None}),
            422,
            "settings_invalid",
        )
        assert _ok(client.post(f"{_SUBSCRIPTIONS}/sub-a/retire"))["state"] == "retired"
        assert _ok(client.get(_SUBSCRIPTIONS))["items"] == []
        assert [i["slug"] for i in _ok(client.get(f"{_SUBSCRIPTIONS}?retired=true"))["items"]] == ["sub-a"]
        assert _ok(client.post(f"{_SUBSCRIPTIONS}/sub-a/retire"))["revision"] == 3
        assert _ok(client.post(f"{_SUBSCRIPTIONS}/sub-a/enable"))["revision"] == 4
        assert [op for _, op, _ in _changes(client, "subscription", "sub-a")] == ["enable", "retire", "edit", "create"]
        _problem(client.get(f"{_SUBSCRIPTIONS}/nope"), 404, "subscription_unknown")


def test_the_prompt_routes_write_the_workspace_record(hosted: HostedApp) -> None:
    prompt = f"{_WORKSPACES}/workspace-local/prompt"
    with TestClient(hosted.app) as client:
        assert _ok(client.get(prompt)) == {"prompt": "SEEDED", "source": "config"}
        assert _ok(client.put(prompt, json={"prompt": "REPLACED"})) == {"prompt": "REPLACED", "source": "config"}
        assert _ok(client.get(f"{_WORKSPACES}/workspace-local"))["settings"] == {"prompt": "REPLACED"}
        assert _ok(client.delete(prompt)) == {"prompt": "", "source": "default"}
        record = _ok(client.get(f"{_WORKSPACES}/workspace-local"))
        assert (record["revision"], record["settings"]) == (3, {})
        # A clear of a workspace with no standing prompt writes nothing.
        assert _ok(client.delete(prompt)) == {"prompt": "", "source": "default"}
        assert _changes(client, "workspace", "workspace-local") == [
            (3, "edit", "api"),
            (2, "edit", "api"),
            (1, "seed", "seed"),
        ]
        for method in ("GET", "PUT", "DELETE"):
            response = client.request(method, f"{_WORKSPACES}/elsewhere/prompt", json={"prompt": "x"})
            _problem(response, 404, "workspace_unknown")


@pytest.mark.parametrize("role", [Role.CONTRIBUTOR, Role.SUPERUSER])
def test_record_writes_need_a_superuser_while_the_prompt_stays_on_the_human_lane(
    hosted: HostedApp, role: Role, tmp_path: Path
) -> None:
    app = hosted.app
    app.state.hub_auth_mode = SimpleNamespace(enabled=lambda: True)
    names = CookieNames(TEST_RUNNER_ID)
    app.state.federation = SimpleNamespace(cookie_names=lambda: names)
    secret = b"test-host-records-session-secret"
    app.state.session_secret = secret
    now = datetime.now(UTC)
    with TestClient(app) as client:
        for headers in ({"X-Blizzard-Lease-Token": "worker-token"}, {"Authorization": "Bearer worker-token"}):
            refused = client.patch(f"{_WORKSPACES}/workspace-local", headers={**headers, "If-Match": "1"}, json={})
            assert refused.status_code == 401, refused.text
        client.cookies.set(
            names.session, SessionCookie(secret).mint(RunnerSession("alice", role, now, now + timedelta(hours=1)))
        )
        assert client.get(_WORKSPACES).status_code == 200
        writes = [
            client.patch(f"{_WORKSPACES}/workspace-local", headers={"If-Match": "1"}, json={"base_branch": "trunk"}),
            client.post(_WORKSPACES, json={"id": "ws-c", "provider": "basic", "root": str(tmp_path / "c")}),
            client.post(_SUBSCRIPTIONS, json={"slug": "sub-c", "name": "C", "provider": PROVIDER_ANTHROPIC}),
            client.post(f"{_SUBSCRIPTIONS}/sub-c/retire"),
            client.post(f"{_SUBSCRIPTIONS}/sub-c/enable"),
            client.post(f"{_WORKSPACES}/ws-c/retire"),
            client.post(f"{_WORKSPACES}/ws-c/enable"),
        ]
        for response in writes:
            assert response.status_code == (403 if role is Role.CONTRIBUTOR else 200), response.text
            if role is Role.CONTRIBUTOR:
                assert response.json()["code"] == "superuser_required"
        prompt = f"{_WORKSPACES}/workspace-local/prompt"
        assert _ok(client.put(prompt, json={"prompt": "BY ALICE"}))["prompt"] == "BY ALICE"
        assert _ok(client.delete(prompt))["source"] == "default"
        rows: list[dict[str, Any]] = _ok(client.get(f"{_CHANGES}?record_kind=workspace&record_key=workspace-local"))[
            "changes"
        ]
        assert [row["actor"] for row in rows][:2] == ["alice", "alice"]


def test_an_app_without_the_hosts_store_refuses_every_record_write(tmp_path: Path) -> None:
    """The store-free app (OpenAPI export, unit boot) refuses a write rather than pretend."""
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", workspaces=one_workspace(prompt="STATIC"))
    with TestClient(create_runner_app(config)) as client:
        assert client.put(f"{_WORKSPACES}/{DEFAULT_WORKSPACE_ID}/prompt", json={"prompt": "x"}).status_code == 503
        assert client.post(_WORKSPACES, json={"id": "ws-x"}).status_code == 503
        assert [i["workspace_id"] for i in _ok(client.get(_WORKSPACES))["items"]] == [DEFAULT_WORKSPACE_ID]
