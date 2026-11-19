"""``/api/host/runners`` over a real host: an add judged as a settings write before the hub is asked, then written to
the store whole and listed ``starting``; a retire asked of the hub before its ``retired`` fact, and a retired runner's
retire writing nothing; the hub's refusals relayed; an unregistered host refused; a halted start listed with its
failure; and every write gated on a superuser session."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from blizzard.auth_core import Role
from blizzard.foundation.clock import SystemClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.app import build_hosted_app
from blizzard.runner.auth.session import CookieNames, RunnerSession, SessionCookie
from blizzard.runner.cli.runtime import StoreHostedRunners, seed_runners
from blizzard.runner.composition import build_host_process, build_host_stores, build_hosted_runner_service
from blizzard.runner.host_config import HaltedRunner, HostConfig
from blizzard.runner.host_keys import ENV_HOST_SECRET_KEY
from blizzard.runner.host_layout import HostLayout
from blizzard.runner.hosting.reconcile import RunnerStartFailure, RunnerStartFailures
from blizzard.runner.hub.client import (
    HostRefused,
    HostRunnerRetireRefused,
    HubClientError,
    IssuedIdentity,
    RetiredHostRunner,
    RunnerAddRefusalReason,
    RunnerAddRefused,
)
from blizzard.runner.hub.sealed_tokens import StoredToken
from tests.support.config_inputs import load_host_config
from tests.support.hub_tokens import host_hub_tokens
from tests.support.runner_fakes import migrated_store_at, runner_store_errors

_RUNNER = "rn_one"


@dataclass
class _Hub:
    """The host's hub calls: each add mints the next id, each retire retires; a set refusal is raised instead."""

    add_refusal: HubClientError | None = None
    retire_refusal: HubClientError | None = None
    added: list[tuple[str, tuple[str, ...] | None]] = field(default_factory=list[tuple[str, tuple[str, ...] | None]])
    retired: list[tuple[str, bool]] = field(default_factory=list[tuple[str, bool]])

    def add_runner(self, name: str, *, projects: tuple[str, ...] | None = None) -> IssuedIdentity | HostRefused:
        if self.add_refusal is not None:
            raise self.add_refusal
        self.added.append((name, projects))
        n = len(self.added)
        return IssuedIdentity(runner_id=f"rn_added{n}", runner_name=name, token=f"tok-added-{n}")

    def retire_runner(self, runner_id: str, *, force: bool = False) -> RetiredHostRunner | HostRefused:
        if self.retire_refusal is not None:
            raise self.retire_refusal
        self.retired.append((runner_id, force))
        return RetiredHostRunner(runner_id=runner_id, released_chunk_ids=("ch_1",) if force else ())


def _host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> HostConfig:
    monkeypatch.delenv(ENV_HOST_SECRET_KEY, raising=False)
    monkeypatch.setenv("BZ_HUB_TOKEN_R_ONE", "tok-one")
    db_url = f"sqlite:///{tmp_path / 'host.db'}"
    migrated_store_at(db_url)
    HostLayout(tmp_path).config_path.write_text(
        f'db_url = "{db_url}"\nhub_url = "http://127.0.0.1:9"\nhost_token = "tok-host"\n\n'
        '[[workspace]]\nid = "workspace-local"\n\n'
        f'[[runner]]\nid = "{_RUNNER}"\nname = "r-one"\nprojects = []\n'
    )
    return load_host_config(tmp_path)


@contextmanager
def _served(config: HostConfig, hub: _Hub, *, registered: bool = True) -> Generator[TestClient]:
    ready, halted = seed_runners(config, clock=SystemClock())
    graph = build_host_process(config, ready, halted=halted)
    hosted = build_hosted_app(
        ready[0].config, host=graph, host_config=config, source=StoreHostedRunners(config, SystemClock())
    )
    hosted.app.state.hub_auth_mode = SimpleNamespace(enabled=lambda: False)
    hosted.app.state.hosted_runners = (
        build_hosted_runner_service(config, graph.engine, hub, clock=SystemClock()) if registered else None
    )
    try:
        with TestClient(hosted.app) as client:
            yield client
    finally:
        hosted.close()
        graph.close()


def _add(client: TestClient, name: str = "night-1", **body: object) -> tuple[int, dict[str, object]]:
    request: dict[str, object] = {"name": name, "projects": [{"project": "default"}], "settings": {"agents": 1}}
    response = client.post("/api/host/runners", json={**request, **body})
    return response.status_code, response.json()


def _listed(client: TestClient) -> dict[str, str]:
    return {item["name"]: item["state"] for item in client.get("/api/host/runners").json()["items"]}


def _hosted(config: HostConfig) -> tuple[tuple[str, ...], frozenset[str]]:
    engine = create_engine_from_url(config.db_url)
    try:
        hosted = build_host_stores(engine, errors=runner_store_errors(), clock=SystemClock()).hosted_runners
        return hosted.active(), hosted.retired()
    finally:
        engine.dispose()


def test_an_add_is_written_whole_and_listed_starting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _host(tmp_path, monkeypatch)
    hub = _Hub()
    with _served(config, hub) as client:
        status, body = _add(client, settings={"agents": 2})

        assert status == 200, body
        assert (body["runner_id"], body["name"], body["state"]) == ("rn_added1", "night-1", "starting")
        assert _listed(client) == {"r-one": "serving", "night-1": "starting"}
    assert hub.added == [("night-1", ("default",))]
    assert _hosted(config) == ((_RUNNER, "rn_added1"), frozenset())
    with host_hub_tokens(config.root, config.db_url) as tokens:
        assert tokens.stored("rn_added1") == StoredToken.opened("tok-added-1", 1)


@pytest.mark.parametrize(
    ("name", "body", "key"),
    [
        ("r-one", {}, "name"),
        ("night-1", {"projects": [{"project": "default", "workspace": "nope"}]}, "projects"),
        ("night-1", {"settings": {"agents": "many"}}, "agents"),
    ],
)
def test_an_invalid_add_names_every_failing_key_with_nothing_minted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, body: dict[str, object], key: str
) -> None:
    config = _host(tmp_path, monkeypatch)
    hub = _Hub()
    with _served(config, hub) as client:
        status, problem = _add(client, name, **body)

    assert status == 422, problem
    assert problem["code"] == "settings_invalid"
    errors = cast(list[dict[str, str]], problem["errors"])
    assert key in {error["key"] for error in errors}
    assert hub.added == []
    assert _hosted(config) == ((_RUNNER,), frozenset())


def test_an_unregistered_host_refuses_an_add(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _host(tmp_path, monkeypatch)
    with _served(config, _Hub(), registered=False) as client:
        status, problem = _add(client)
        retire = client.post(f"/api/host/runners/{_RUNNER}/retire", json={})

    assert (status, problem["code"]) == (409, "host_unregistered")
    assert (retire.status_code, retire.json()["code"]) == (409, "host_unregistered")


@pytest.mark.parametrize(
    ("refusal", "expected"),
    [
        (
            RunnerAddRefused(
                "422", reason=RunnerAddRefusalReason.NO_SUCH_PROJECT, detail="no project default in this tenant"
            ),
            (409, "hub_refused"),
        ),
        (HubClientError("connection refused"), (503, "hub_unreachable")),
    ],
)
def test_the_hubs_refusal_of_an_add_is_relayed_with_nothing_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, refusal: HubClientError, expected: tuple[int, str]
) -> None:
    config = _host(tmp_path, monkeypatch)
    with _served(config, _Hub(add_refusal=refusal)) as client:
        status, problem = _add(client)

    assert (status, problem["code"]) == expected
    assert _hosted(config) == ((_RUNNER,), frozenset())


def test_a_retire_asks_the_hub_then_records_the_runner_retired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _host(tmp_path, monkeypatch)
    hub = _Hub()
    with _served(config, hub) as client:
        response = client.post(f"/api/host/runners/{_RUNNER}/retire", json={"force": True})

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["state"], body["released_chunk_ids"], body["already"]) == ("retired", ["ch_1"], False)
    assert hub.retired == [(_RUNNER, True)]
    assert _hosted(config) == ((), frozenset({_RUNNER}))


def test_a_retired_runners_retire_writes_nothing_and_asks_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _host(tmp_path, monkeypatch)
    hub = _Hub()
    with _served(config, hub) as client:
        assert client.post(f"/api/host/runners/{_RUNNER}/retire", json={}).status_code == 200
        again = client.post(f"/api/host/runners/{_RUNNER}/retire", json={})

    assert again.status_code == 200, again.text
    assert (again.json()["state"], again.json()["already"]) == ("retired", True)
    assert hub.retired == [(_RUNNER, False)]


def test_a_retire_of_a_runner_the_host_does_not_hold_is_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _host(tmp_path, monkeypatch)
    hub = _Hub()
    with _served(config, hub) as client:
        response = client.post("/api/host/runners/rn_nobody/retire", json={})

    assert (response.status_code, response.json()["code"]) == (404, "runner_unknown")
    assert hub.retired == []


def test_the_hubs_refusal_of_a_retire_is_relayed_with_nothing_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _host(tmp_path, monkeypatch)
    refusal = HostRunnerRetireRefused("409", detail="runner rn_one holds 1 chunk(s): ch_1 — retire with --force")
    with _served(config, _Hub(retire_refusal=refusal)) as client:
        response = client.post(f"/api/host/runners/{_RUNNER}/retire", json={})

    assert (response.status_code, response.json()["code"]) == (409, "hub_refused")
    assert "--force" in response.json()["detail"]
    assert _hosted(config) == ((_RUNNER,), frozenset())


def test_a_failed_start_is_listed_with_its_step_and_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _host(tmp_path, monkeypatch)
    with _served(config, _Hub()) as client:
        app = cast(FastAPI, client.app)
        failures = RunnerStartFailures()
        failures.hold("rn_broken", RunnerStartFailure("worker_shell", "no shell"))
        app.state.start_failures = failures
        app.state.runners.halt(HaltedRunner("broken", "rn_broken", ("failed to start at worker_shell: no shell",)))

        items = {item["name"]: item for item in client.get("/api/host/runners").json()["items"]}

    assert items["broken"]["state"] == "start_failed"
    assert items["broken"]["start_failure"] == {"step": "worker_shell", "error": "no shell"}
    assert items["r-one"]["start_failure"] is None


def test_a_runner_only_the_store_hosts_takes_a_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _host(tmp_path, monkeypatch)
    with _served(config, _Hub()) as client:
        assert _add(client)[0] == 200
        response = client.put("/api/host/runners/rn_added1/token", json={"token": "tok-replaced"})

    assert response.status_code == 204, response.text
    with host_hub_tokens(config.root, config.db_url) as tokens:
        assert tokens.stored("rn_added1") == StoredToken.opened("tok-replaced", 2)


@pytest.mark.parametrize("write", ["add", "retire"])
def test_every_write_requires_a_superuser_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, write: str) -> None:
    config = _host(tmp_path, monkeypatch)
    hub = _Hub()
    with _served(config, hub) as client:
        app = cast(FastAPI, client.app)
        app.state.hub_auth_mode = SimpleNamespace(enabled=lambda: True)
        names = CookieNames(_RUNNER)
        app.state.federation = SimpleNamespace(cookie_names=lambda: names)
        secret = b"test-host-runners-session-secret"
        app.state.session_secret = secret
        now = datetime.now(UTC)
        client.cookies.set(
            names.session,
            SessionCookie(secret).mint(RunnerSession("alice", Role.CONTRIBUTOR, now, now + timedelta(hours=1))),
        )
        if write == "add":
            response = client.post("/api/host/runners", json={"name": "night-1", "projects": [{"project": "d"}]})
        else:
            response = client.post(f"/api/host/runners/{_RUNNER}/retire", json={})

    assert response.status_code == 403, response.text
    assert response.json()["code"] == "superuser_required"
    assert (hub.added, hub.retired) == ([], [])
