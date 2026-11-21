"""The host settings document's apply and export over the real store, as the production host wires it."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from blizzard.foundation.clock import SystemClock
from blizzard.foundation.toml_emit import toml_document
from blizzard.runner.api.settings import SettingsProblem
from blizzard.runner.app import HostedApp, build_hosted_app
from blizzard.runner.cli.runtime import seed_host, seed_runners
from blizzard.runner.composition import HostProcess, build_host_process
from blizzard.runner.host_config import decode_host_text
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.store.internal.identity_store import RunnerIdentityStore
from tests.support.config_inputs import load_host_config
from tests.support.host_files import TEST_RUNNER_ID, TEST_WORKSPACE_ID, write_minimal_host
from tests.support.runner_fakes import make_store

_APPLY = "/api/host/settings/apply"
_EXPORT = "/api/host/settings/export"
_CHANGES = "/api/host/settings/changes"


@pytest.fixture
def hosted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[tuple[HostProcess, HostedApp, Path]]:
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
    host = build_host_process(config, ready)
    app = build_hosted_app(host.runners[0].config, host)
    try:
        yield host, app, tmp_path
    finally:
        app.close()
        host.close()


def _state(client: TestClient) -> dict[str, Any]:
    """Every revision, record and change row a document can move."""
    return {
        "host": client.get("/api/host/settings").json()["revision"],
        "runner": client.get(f"/runners/{TEST_RUNNER_ID}/api/settings").json()["revision"],
        "workspaces": client.get("/api/host/workspaces").json(),
        "changes": client.get(f"{_CHANGES}?limit=100").json()["changes"],
        "runner_changes": client.get(f"/runners/{TEST_RUNNER_ID}/api/settings/changes?limit=100").json()["changes"],
    }


def _problem(response: Any, code: str) -> SettingsProblem:
    assert response.status_code == 422, response.text
    body = SettingsProblem.model_validate(response.json())
    assert body.code == code
    return body


def _workspace(tmp_path: Path, workspace_id: str) -> dict[str, object]:
    root = tmp_path / workspace_id
    root.mkdir()
    return {"id": workspace_id, "root": str(root)}


def test_a_refused_entry_fails_the_whole_document_and_writes_nothing(
    hosted: tuple[HostProcess, HostedApp, Path],
) -> None:
    _, app, tmp_path = hosted
    with TestClient(app.app) as client:
        before = _state(client)
        refused = client.post(
            _APPLY,
            json={
                "tracing": {"batch_limit": 40},
                "workspace": [_workspace(tmp_path, "ws_new")],
                "runner": [{"id": "rn_unknown", "agents": 2}],
            },
        )
        problem = _problem(refused, "runner_unknown")
        assert (problem.record_kind, problem.record_key) == ("runner", "rn_unknown")
        immutable = client.post(_APPLY, json={"runner": [{"id": TEST_RUNNER_ID}], "port": 1})
        assert _problem(immutable, "setting_not_live").record_kind == "host"
        malformed = client.post(_APPLY, json={"runner": {"id": TEST_RUNNER_ID}})
        assert _problem(malformed, "settings_invalid").errors is not None
        # A dry run reaches the resolved validation a real apply reaches.
        invalid = client.post(f"{_APPLY}?dry_run=true", json={"tracing": {"batch_limit": 0}})
        assert [error.key for error in _problem(invalid, "settings_invalid").errors or []] == ["tracing.batch_limit"]
        assert _state(client) == before


def test_a_dry_run_answers_the_outcomes_and_writes_nothing(hosted: tuple[HostProcess, HostedApp, Path]) -> None:
    _, app, tmp_path = hosted
    with TestClient(app.app) as client:
        before = _state(client)
        document = {
            "tracing": {"batch_limit": 40},
            "tick_seconds": 5,
            "workspace": [_workspace(tmp_path, "ws_new")],
            "runner": [{"id": TEST_RUNNER_ID, "agents": 3}],
        }
        preview = client.post(f"{_APPLY}?dry_run=true", json=document)
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert (body["dry_run"], body["apply_id"]) == (True, None)
        assert [(o["kind"], o["key"], o["op"]) for o in body["outcomes"]] == [
            ("host", "tracing.batch_limit", "edit"),
            ("host", "tick_seconds", "unchanged"),
            ("workspace", "ws_new", "create"),
            ("runner", TEST_RUNNER_ID, "edit"),
        ]
        assert [(d["field"], d["old"], d["new"]) for d in body["outcomes"][0]["diff"]] == [
            ("tracing.batch_limit", 10, 40)
        ]
        assert _state(client) == before


def test_an_apply_writes_every_row_under_door_apply_and_one_apply_id(
    hosted: tuple[HostProcess, HostedApp, Path],
) -> None:
    host, app, tmp_path = hosted
    signal = host.wakes.signal()
    with TestClient(app.app) as client:
        before = _state(client)
        applied = client.post(
            _APPLY,
            json={
                "tick_seconds": 3,
                "workspace": [_workspace(tmp_path, "ws_new")],
                "runner": [{"id": TEST_RUNNER_ID, "agents": 3}],
            },
        )
        assert applied.status_code == 200, applied.text
        apply_id = applied.json()["apply_id"]
        assert apply_id.startswith("apl")
        assert signal.event.is_set()
        after = _state(client)
        rows = after["changes"][: len(after["changes"]) - len(before["changes"])]
        assert sorted((row["record_kind"], row["record_key"]) for row in rows) == [
            ("host", None),
            ("workspace", "ws_new"),
        ]
        runner_rows = after["runner_changes"][: len(after["runner_changes"]) - len(before["runner_changes"])]
        assert len(runner_rows) == 1
        assert {(row["door"], row["apply_id"]) for row in (*rows, *runner_rows)} == {("apply", apply_id)}
        assert "ws_new" in {workspace["workspace_id"] for workspace in after["workspaces"]["items"]}
        # An omitted record is left as it stands.
        assert TEST_WORKSPACE_ID in {workspace["workspace_id"] for workspace in after["workspaces"]["items"]}


def test_an_export_leaves_out_every_default_and_applies_back_moving_nothing(
    hosted: tuple[HostProcess, HostedApp, Path],
) -> None:
    _, app, _ = hosted
    with TestClient(app.app) as client:
        revision = client.get("/api/host/settings").json()["revision"]
        unset = client.patch(
            "/api/host/settings", headers={"If-Match": str(revision)}, json={"unset": ["tick_seconds"]}
        )
        assert unset.status_code == 200, unset.text
        document = client.get(_EXPORT).json()
        assert "tick_seconds" not in document
        assert document["tracing"] == {"batch_limit": 10}
        assert [runner["id"] for runner in document["runner"]] == [TEST_RUNNER_ID]
        assert [workspace["id"] for workspace in document["workspace"]] == [TEST_WORKSPACE_ID]
        before = _state(client)
        again = client.post(_APPLY, json=decode_host_text(toml_document(document), what="export"))
        assert again.status_code == 200, again.text
        assert {outcome["op"] for outcome in again.json()["outcomes"]} == {"unchanged"}
        assert _state(client) == before
