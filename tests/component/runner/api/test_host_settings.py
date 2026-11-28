"""The host settings HTTP boundary over the real host record, as the production host wires it."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx2
import pytest
from fastapi.testclient import TestClient

from blizzard.auth_core import Role
from blizzard.foundation.clock import SystemClock
from blizzard.runner.api.settings import SettingsProblem
from blizzard.runner.app import HostedApp, build_hosted_app
from blizzard.runner.auth.session import CookieNames, RunnerSession, SessionCookie
from blizzard.runner.cli.runtime import seed_host, seed_runners
from blizzard.runner.composition import HostProcess
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.store.internal.identity_store import RunnerIdentityStore
from tests.support.composed_host import open_host
from tests.support.config_inputs import load_host_config
from tests.support.host_files import TEST_RUNNER_ID, write_minimal_host
from tests.support.runner_fakes import make_store

_ROUTE = "/api/host/settings"


@pytest.fixture
def hosted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[tuple[HostProcess, HostedApp]]:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    url = f"sqlite:///{tmp_path / 'runner.db'}"
    write_minimal_host(tmp_path, top=f'db_url = "{url}"\ntick_seconds = 5\n[tracing]\nbatch_limit = 10')
    store = make_store(url, TEST_RUNNER_ID)
    config = load_host_config(tmp_path)
    RunnerIdentityStore(store.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, config.runners[0].name, datetime.now(UTC))
    )
    config = seed_host(config, {}, clock=SystemClock())
    ready, halted = seed_runners(config, clock=SystemClock())
    assert not halted
    opened = open_host(config, ready)
    host = opened.host
    app = build_hosted_app(host.runners[0].config, opened)
    try:
        yield host, app
    finally:
        app.close()
        opened.close()


def _problem(response: httpx2.Response, status: int, code: str) -> SettingsProblem:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    body = SettingsProblem.model_validate(response.json())
    assert body.code == code
    return body


def _failing(problem: SettingsProblem) -> list[str]:
    assert problem.errors is not None
    return [error.key for error in problem.errors]


def test_the_host_view_lists_every_host_key_with_its_source_and_in_force(
    hosted: tuple[HostProcess, HostedApp],
) -> None:
    host, app = hosted
    with TestClient(app.app) as client:
        view = client.get(_ROUTE).json()
        rows = {row["key"]: row for row in view["settings"]}
        held = host.host_settings
        assert held is not None
        assert set(rows) == set(held.reader.keys)
        assert "port" not in rows and "db_url" not in rows
        assert rows["tick_seconds"] | {"written_at": None} == {
            "key": "tick_seconds",
            "value": 5,
            "source": "entry",
            "lifetime": "pass",
            "revision": 1,
            "written_at": None,
            "written_by": "seed",
        }
        assert rows["tracing.batch_limit"]["value"] == 10
        assert rows["tracing.batch_limit"]["source"] == "entry"
        assert rows["tracing.batch_limit"]["revision"] == 2
        assert rows["tracing.batch_limit"]["written_by"] == "seed"
        assert rows["tracing.batch_limit"]["written_at"]
        assert (rows["tracing.sweep_seconds"]["value"], rows["tracing.sweep_seconds"]["source"]) == (60, "default")
        assert rows["tools.path_prepend"] == {
            "key": "tools.path_prepend",
            "value": [],
            "source": "default",
            "lifetime": "session",
            "revision": None,
            "written_at": None,
            "written_by": None,
        }
        # The host tick's own snapshot puts a revision in force, not the write.
        assert view["in_force"] == {"revision": None, "since": None, "failed": None}
        assert app.host_stepper is not None
        app.host_stepper.request()
        in_force = client.get(_ROUTE).json()["in_force"]
        assert in_force["revision"] == view["revision"]
        assert in_force["since"]
        assert in_force["failed"] is None


def test_host_writes_are_revision_conditioned_refused_whole_and_wake_the_drivers(
    hosted: tuple[HostProcess, HostedApp],
) -> None:
    host, app = hosted
    signal = host.wakes.signal()
    with TestClient(app.app) as client:
        revision = client.get(_ROUTE).json()["revision"]
        malformed = client.patch(_ROUTE, content="{", headers={"content-type": "application/json"})
        assert malformed.status_code == 422
        assert malformed.headers["content-type"] == "application/json"
        assert malformed.json()["detail"][0]["type"] == "json_invalid"
        missing = client.patch(_ROUTE, json={"set": {"tick_seconds": 2}})
        assert _problem(missing, 409, "revision_conflict").current == revision
        stale = client.patch(_ROUTE, headers={"If-Match": str(revision - 1)}, json={"set": {"tick_seconds": 2}})
        _problem(stale, 409, "revision_conflict")
        for key, code in [
            ("port", "setting_not_live"),
            ("tracing.platform", "setting_not_live"),
            ("subscription[].slug", "setting_unknown"),
            ("agents", "setting_unknown"),
            ("bogus", "setting_unknown"),
        ]:
            refused = client.patch(_ROUTE, headers={"If-Match": str(revision)}, json={"set": {key: 1}})
            assert _problem(refused, 422, code).key == key
        assert "blizzard-host.toml" in str(
            client.patch(_ROUTE, headers={"If-Match": str(revision)}, json={"set": {"port": 1}}).json()["detail"]
        )
        both = client.patch(
            _ROUTE, headers={"If-Match": str(revision)}, json={"set": {"tick_seconds": 2}, "unset": ["tick_seconds"]}
        )
        assert _failing(_problem(both, 422, "settings_invalid")) == ["tick_seconds"]
        invalid = client.patch(
            _ROUTE,
            headers={"If-Match": str(revision)},
            json={"set": {"tick_seconds": -1, "tracing.batch_limit": 0, "tracing.settle_seconds": 3}},
        )
        assert set(_failing(_problem(invalid, 422, "settings_invalid"))) == {"tick_seconds", "tracing.batch_limit"}
        assert client.get(_ROUTE).json()["revision"] == revision
        assert not signal.event.is_set()

        quiet = client.patch(_ROUTE, headers={"If-Match": str(revision)}, json={"set": {"tracing.batch_limit": 20}})
        assert quiet.status_code == 200, quiet.text
        assert quiet.json()["revision"] == revision + 1
        # A write that moves no interval leaves every driver asleep.
        assert not signal.event.is_set()
        moved = client.patch(_ROUTE, headers={"If-Match": str(revision + 1)}, json={"set": {"tick_seconds": 2}})
        assert moved.status_code == 200, moved.text
        row = next(r for r in moved.json()["settings"] if r["key"] == "tick_seconds")
        assert (row["value"], row["source"], row["revision"], row["written_by"]) == (
            2,
            "entry",
            revision + 2,
            "operator",
        )
        # The route wakes nothing itself: the authoring service the production host builds does, after commit.
        assert signal.event.is_set()
        unset = client.patch(_ROUTE, headers={"If-Match": str(revision + 2)}, json={"unset": ["tick_seconds"]})
        row = next(r for r in unset.json()["settings"] if r["key"] == "tick_seconds")
        assert (row["value"], row["source"]) == (30.0, "default")


def test_host_changes_page_newest_first_and_filter_by_record(hosted: tuple[HostProcess, HostedApp]) -> None:
    _, app = hosted
    with TestClient(app.app) as client:
        revision = client.get(_ROUTE).json()["revision"]
        for value in (11, 12, 13):
            response = client.patch(
                _ROUTE, headers={"If-Match": str(revision)}, json={"set": {"tracing.batch_limit": value}}
            )
            assert response.status_code == 200, response.text
            revision += 1
        first = client.get(f"{_ROUTE}/changes?limit=2&record_kind=host").json()
        assert [c["revision"] for c in first["changes"]] == [revision, revision - 1]
        newest = first["changes"][0]
        assert (newest["record_kind"], newest["record_key"], newest["door"], newest["op"]) == (
            "host",
            None,
            "api",
            "edit",
        )
        assert newest["diff"] == [
            {
                "key": "tracing.batch_limit",
                "old": {"value": 12, "at_default": False},
                "new": {"value": 13, "at_default": False},
            }
        ]
        rest = client.get(f"{_ROUTE}/changes?limit=2&record_kind=host&before={first['next_before']}").json()
        assert [c["revision"] for c in rest["changes"]] == [revision - 2, revision - 3]
        last = client.get(f"{_ROUTE}/changes?limit=2&record_kind=host&before={rest['next_before']}").json()
        # The seed wrote one row per key the file states.
        assert [(c["revision"], c["door"]) for c in last["changes"]] == [(1, "seed")]
        assert last["next_before"] is None
        assert len(client.get(f"{_ROUTE}/changes?record_kind=host").json()["changes"]) == revision
        # The seed wrote one row for the file's one workspace, under its own kind and key.
        seeded = client.get(f"{_ROUTE}/changes?record_kind=workspace").json()["changes"]
        assert [(c["revision"], c["op"], c["door"]) for c in seeded] == [(1, "seed", "seed")]
        assert client.get(f"{_ROUTE}/changes?record_key=ws_1").json()["changes"] == []
        assert client.get(f"{_ROUTE}/changes?limit=0").status_code == 422
        spec = app.app.openapi()
        schema = spec["components"]["schemas"]["HostChangeView"]
        assert {"record_kind", "record_key"} <= set(schema["required"])
        assert "record_kind" not in spec["components"]["schemas"]["ChangeView"]["properties"]
        params = {p["name"] for p in spec["paths"][f"{_ROUTE}/changes"]["get"]["parameters"]}
        assert params == {"limit", "before", "record_kind", "record_key"}


@pytest.mark.parametrize("gated", [False, True])
def test_host_settings_human_roles_and_worker_credential(hosted: tuple[HostProcess, HostedApp], gated: bool) -> None:
    _, hosted_app = hosted
    app = hosted_app.app
    app.state.hub_auth_mode = SimpleNamespace(enabled=lambda: gated)
    names = CookieNames(TEST_RUNNER_ID)
    app.state.federation = SimpleNamespace(cookie_names=lambda: names)
    secret = b"test-host-settings-session-secret"
    app.state.session_secret = secret
    now = datetime.now(UTC)
    with TestClient(app) as client:
        revision = client.get(_ROUTE).json()["revision"] if not gated else None
        for headers in ({"X-Blizzard-Lease-Token": "worker-token"}, {"Authorization": "Bearer worker-token"}):
            for method, path in [("get", _ROUTE), ("get", f"{_ROUTE}/changes"), ("patch", _ROUTE)]:
                response = client.request(
                    method, path, headers={**headers, "If-Match": "1"}, json={"set": {"tick_seconds": 2}}
                )
                assert response.status_code == 401, response.text
        if not gated:
            response = client.patch(_ROUTE, headers={"If-Match": str(revision)}, json={"set": {"tick_seconds": 2}})
            assert response.status_code == 200, response.text
        for role in (Role.CONTRIBUTOR, Role.SUPERUSER) if gated else ():
            client.cookies.set(
                names.session, SessionCookie(secret).mint(RunnerSession("alice", role, now, now + timedelta(hours=1)))
            )
            view = client.get(_ROUTE)
            assert view.status_code == 200
            assert client.get(f"{_ROUTE}/changes").status_code == 200
            response = client.patch(
                _ROUTE, headers={"If-Match": str(view.json()["revision"])}, json={"set": {"tick_seconds": 2}}
            )
            assert response.status_code == (403 if role is Role.CONTRIBUTOR else 200), response.text
            if role is Role.CONTRIBUTOR:
                assert response.json()["code"] == "superuser_required"
            else:
                row = next(r for r in response.json()["settings"] if r["key"] == "tick_seconds")
                assert row["written_by"] == "alice"
