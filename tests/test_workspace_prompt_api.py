"""The runtime workspace-prompt control — ``GET``/``PUT /api/workspace-prompt`` (issue #17).

The runner prepends a standing workspace prompt to every worker spawn; its static source
is config, and this local-API edge is the runtime control. ``GET`` reports the effective
prompt (the store override when set, else static config); ``PUT`` replaces the override so
subsequent spawns pick it up with no restart. Exercised over a real store via TestClient.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig
from tests.runner_fakes import make_store


def _app_with_store(tmp_path: Path, *, workspace_prompt: str = ""):  # type: ignore[no-untyped-def]
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(
        root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}", workspace_prompt=workspace_prompt
    )
    return create_app(config, runner_store=store), store, config


@pytest.mark.component
def test_get_returns_static_config_prompt_without_an_override(tmp_path: Path) -> None:
    app, _store, _config = _app_with_store(tmp_path, workspace_prompt="STATIC")
    with TestClient(app) as client:
        resp = client.get("/api/workspace-prompt")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"prompt": "STATIC"}


@pytest.mark.component
def test_put_replaces_override_and_get_reflects_it(tmp_path: Path) -> None:
    app, store, config = _app_with_store(tmp_path, workspace_prompt="STATIC")
    with TestClient(app) as client:
        put = client.put("/api/workspace-prompt", json={"prompt": "REPLACED"})
        assert put.status_code == 200, put.text
        assert put.json() == {"prompt": "REPLACED"}
        # The override is durable in the store (what the loop reads at spawn) and GET reflects it.
        assert store.workspace_prompt_override(config.workspace_id) == "REPLACED"
        assert client.get("/api/workspace-prompt").json() == {"prompt": "REPLACED"}


@pytest.mark.component
def test_put_can_clear_to_table_only(tmp_path: Path) -> None:
    # An empty replacement is a deliberate clear — a present override, not a fall-back to static.
    app, store, config = _app_with_store(tmp_path, workspace_prompt="STATIC")
    with TestClient(app) as client:
        client.put("/api/workspace-prompt", json={"prompt": ""})
        assert client.get("/api/workspace-prompt").json() == {"prompt": ""}
    assert store.workspace_prompt_override(config.workspace_id) == ""


@pytest.mark.component
def test_put_503_when_store_unwired(tmp_path: Path) -> None:
    """The store-free app (OpenAPI export / unit boot) refuses the write rather than pretend."""
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", workspace_prompt="STATIC")
    with TestClient(create_app(config)) as client:
        resp = client.put("/api/workspace-prompt", json={"prompt": "x"})
    assert resp.status_code == 503
