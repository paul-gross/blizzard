"""The runtime workspace-prompt control — ``GET``/``PUT``/``DELETE`` (#17, #344).

``GET`` reports the effective prompt and which lane produced it (the store override
when set, else static config); ``PUT`` replaces the override, and ``DELETE`` drops it
so config resolves again. Exercised over a real store via TestClient.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig
from tests.runner_fakes import make_store, make_stores


def _app_with_store(tmp_path: Path, *, workspace_prompt: str = ""):  # type: ignore[no-untyped-def]
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(
        root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}", workspace_prompt=workspace_prompt
    )
    return create_app(config, runner_stores=make_stores(store)), store, config


@pytest.mark.component
def test_get_returns_static_config_prompt_without_an_override(tmp_path: Path) -> None:
    app, _store, _config = _app_with_store(tmp_path, workspace_prompt="STATIC")
    with TestClient(app) as client:
        resp = client.get("/api/workspace-prompt")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"prompt": "STATIC", "source": "config"}


@pytest.mark.component
def test_put_replaces_override_and_get_reflects_it(tmp_path: Path) -> None:
    app, store, config = _app_with_store(tmp_path, workspace_prompt="STATIC")
    with TestClient(app) as client:
        put = client.put("/api/workspace-prompt", json={"prompt": "REPLACED"})
        assert put.status_code == 200, put.text
        assert put.json() == {"prompt": "REPLACED", "source": "override"}
        # The override is durable in the store (what the loop reads at spawn) and GET reflects it.
        assert store.workspace_prompt_override(config.workspace_id) == "REPLACED"
        assert client.get("/api/workspace-prompt").json() == {"prompt": "REPLACED", "source": "override"}


@pytest.mark.component
def test_put_can_clear_to_table_only(tmp_path: Path) -> None:
    # An empty replacement is a deliberate clear — a present override, not a fall-back to static.
    app, store, config = _app_with_store(tmp_path, workspace_prompt="STATIC")
    with TestClient(app) as client:
        client.put("/api/workspace-prompt", json={"prompt": ""})
        assert client.get("/api/workspace-prompt").json() == {"prompt": "", "source": "override"}
    assert store.workspace_prompt_override(config.workspace_id) == ""


@pytest.mark.component
def test_delete_drops_the_override_and_config_resolves_again(tmp_path: Path) -> None:
    """The one path back from an override — distinct from PUTting empty text, which stands."""
    app, store, config = _app_with_store(tmp_path, workspace_prompt="STATIC")
    with TestClient(app) as client:
        client.put("/api/workspace-prompt", json={"prompt": "REPLACED"})
        deleted = client.delete("/api/workspace-prompt")
        assert deleted.status_code == 200, deleted.text
        assert deleted.json() == {"prompt": "STATIC", "source": "config"}
        assert client.get("/api/workspace-prompt").json() == {"prompt": "STATIC", "source": "config"}
    assert store.workspace_prompt_override(config.workspace_id) is None


@pytest.mark.component
def test_delete_without_an_override_is_a_no_op(tmp_path: Path) -> None:
    app, _store, _config = _app_with_store(tmp_path, workspace_prompt="STATIC")
    with TestClient(app) as client:
        assert client.delete("/api/workspace-prompt").json() == {"prompt": "STATIC", "source": "config"}


@pytest.mark.component
def test_put_503_when_store_unwired(tmp_path: Path) -> None:
    """The store-free app (OpenAPI export / unit boot) refuses the write rather than pretend."""
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", workspace_prompt="STATIC")
    with TestClient(create_app(config)) as client:
        resp = client.put("/api/workspace-prompt", json={"prompt": "x"})
    assert resp.status_code == 503
