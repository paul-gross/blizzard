"""The operator CLI and settings API share their real wire contract."""

import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from blizzard.cli.collaborators import CliCollaborators
from blizzard.foundation.clock import SystemClock
from blizzard.runner.app import build_hosted_app
from blizzard.runner.cli import runner as runner_group
from blizzard.runner.cli.runtime import seed_host, seed_runners
from blizzard.runner.composition import build_host_process
from blizzard.runner.config_keys import HOST_REGISTRY
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.loop_wiring import RunnerStepper
from blizzard.runner.settings.authoring import RunnerSettingsAuthoring
from blizzard.runner.settings_reader import RunnerSettingsReader
from blizzard.runner.store.internal.identity_store import RunnerIdentityStore
from blizzard.runner.store.internal.settings_store import RunnerSettingsStore
from tests.support.config_inputs import create_runner_app, load_host_config
from tests.support.host_files import TEST_RUNNER_ID, write_minimal_host
from tests.support.runner_client import runner_seams
from tests.support.runner_fakes import make_store


def test_cli_and_real_settings_routes_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    url = f"sqlite:///{tmp_path / 'host.db'}"
    write_minimal_host(tmp_path, top=f'db_url = "{url}"')
    host = load_host_config(tmp_path)
    base = make_store(url, TEST_RUNNER_ID)
    RunnerIdentityStore(base.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, host.runners[0].name, datetime.now(UTC))
    )
    store = RunnerSettingsStore(base.connections, SystemClock())
    reader = RunnerSettingsReader(host, host.runners[0], store)
    assert host.runners[0].authored is not None
    assert host.runners[0].own is not None
    store.seed(host.runners[0].authored, reader.seed_keys({}), validate=reader.validate)
    app = create_runner_app(
        host.config_for_runner(host.runners[0], host.runners[0].own, host.token_for(host.runners[0]).value)
    )
    runner_seams(app).settings_reader = reader
    runner_seams(app).settings_authoring = RunnerSettingsAuthoring(reader, store, HOST_REGISTRY)
    ready, halted = seed_runners(host, list(host.runners), clock=SystemClock())
    assert not halted
    graph = build_host_process(host, ready)
    stepper = RunnerStepper(graph.runners[0].config, process_graph=graph.runners[0])
    runner_seams(app).stepper = stepper

    race = False

    class RacingClient(TestClient):
        def patch(self, url: Any, **kwargs: Any) -> Any:
            if race:
                with TestClient(app) as other:
                    response = other.patch(url, headers=kwargs["headers"], json={"set": {"agents": 3}})
                    assert response.status_code == 200, response.text
            return super().patch(url, **kwargs)

    def factory(
        *,
        base_url: str = "",
        timeout: float | None = None,
        headers: Mapping[str, str] | None = None,
        uds: str | None = None,
    ) -> TestClient:
        return RacingClient(app, base_url=base_url, headers=dict(headers) if headers else None)

    collaborators = replace(CliCollaborators.production(), client_factory=factory)
    cli = CliRunner()

    def invoke(*args: str):
        return cli.invoke(runner_group, ["settings", *args, "--runner-url", "http://testserver"], obj=collaborators)

    try:
        before = invoke("get")
        assert before.exit_code == 0, before.output
        revision = json.loads(before.output)["revision"]
        changed = invoke("set", "models.aliases", '{ quick = "opus", deep = "sonnet" }')
        assert changed.exit_code == 0, changed.output
        assert json.loads(changed.output)["revision"] == revision + 1
        for alias in ("get", "show", "read"):
            result = invoke(alias, "models.aliases")
            assert result.exit_code == 0, result.output
            assert json.loads(result.output)["value"] == {"quick": "opus", "deep": "sonnet"}
        invalid = invoke("set", "agents", '"bad"')
        assert invalid.exit_code != 0
        assert "agents" in invalid.output
        changes = invoke("changes")
        assert changes.exit_code == 0, changes.output
        assert json.loads(changes.output)["changes"][0]["revision"] == revision + 1
        unset = invoke("unset", "models.aliases")
        assert unset.exit_code == 0, unset.output
        assert json.loads(unset.output)["revision"] == revision + 2
        assert json.loads(invoke("get", "models.aliases").output)["value"] == {}
        race = True
        conflict = invoke("set", "agents", "4")
        race = False
        assert conflict.exit_code != 0
        assert "revision" in conflict.output.lower()
        assert json.loads(invoke("get", "agents").output)["value"] == 3
        assert json.loads(invoke("changes").output)["changes"][0]["revision"] == revision + 3
    finally:
        stepper.close()
        graph.close()


def test_cli_host_settings_round_trip_through_the_real_host_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    url = f"sqlite:///{tmp_path / 'host.db'}"
    write_minimal_host(tmp_path, top=f'db_url = "{url}"\ntick_seconds = 5')
    base = make_store(url, TEST_RUNNER_ID)
    host = load_host_config(tmp_path)
    RunnerIdentityStore(base.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, host.runners[0].name, datetime.now(UTC))
    )
    host = seed_host(host, {})
    ready, halted = seed_runners(host, list(host.runners), clock=SystemClock())
    assert not halted
    graph = build_host_process(host, ready)
    hosted = build_hosted_app(graph.runners[0].config, graph)
    paths: list[str] = []

    class RecordingClient(TestClient):
        def request(self, method: str, url: Any, **kwargs: Any) -> Any:
            paths.append(str(url))
            return super().request(method, url, **kwargs)

    def factory(
        *,
        base_url: str = "",
        timeout: float | None = None,
        headers: Mapping[str, str] | None = None,
        uds: str | None = None,
    ) -> TestClient:
        return RecordingClient(hosted.app, base_url=base_url, headers=dict(headers) if headers else None)

    collaborators = replace(CliCollaborators.production(), client_factory=factory)
    cli = CliRunner()

    def invoke(*args: str):
        return cli.invoke(
            runner_group, ["settings", *args, "--host", "--runner-url", "http://testserver"], obj=collaborators
        )

    try:
        row = invoke("get", "tick_seconds")
        assert row.exit_code == 0, row.output
        assert json.loads(row.stdout)["value"] == 5
        assert json.loads(row.stdout)["source"] == "entry"
        revision = json.loads(invoke("get").stdout)["revision"]
        changed = invoke("set", "tracing.worker_program_services", '{ winter_cli = "winter-blizzard" }')
        assert changed.exit_code == 0, changed.output
        assert json.loads(changed.stdout)["revision"] == revision + 1
        assert json.loads(invoke("get", "tracing.worker_program_services").stdout)["value"] == {
            "winter_cli": "winter-blizzard"
        }
        refused = invoke("set", "port", "9")
        assert refused.exit_code != 0
        assert "blizzard-host.toml" in refused.output
        invalid = invoke("set", "tick_seconds", "0")
        assert invalid.exit_code != 0
        assert "tick_seconds" in invalid.output
        unset = invoke("unset", "tick_seconds")
        assert unset.exit_code == 0, unset.output
        assert json.loads(invoke("get", "tick_seconds").stdout)["source"] == "default"
        changes = json.loads(invoke("changes").stdout)["changes"]
        assert [c["revision"] for c in changes[:2]] == [revision + 2, revision + 1]
        assert {c["record_kind"] for c in changes} == {"host"}
        # The host's record is reached at the host root, never under a runner's prefix, and names no runner.
        assert paths and all(path.startswith("/api/host/settings") for path in paths)
        both = cli.invoke(
            runner_group,
            ["settings", "get", "--host", "--runner", "runner-local", "--runner-url", "http://testserver"],
            obj=collaborators,
        )
        assert both.exit_code == 2
        assert "mutually exclusive" in both.output
    finally:
        hosted.close()
        graph.close()
