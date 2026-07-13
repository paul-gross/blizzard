"""Runtime config — the store URL is the single portability knob (D-095, ``bzh:sql-portable``).

Both daemons scaffold a sqlite default under the data dir and read any store URL
back verbatim: a postgres URL is accepted with no code branch on the backend, and
the winter service band ``BZ_*_PORT`` env overrides the bind port.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.hub.config import ENV_PORT as HUB_ENV_PORT
from blizzard.hub.config import HubConfig
from blizzard.runner.config import ENV_PORT as RUNNER_ENV_PORT
from blizzard.runner.config import RunnerConfig


@pytest.mark.unit
def test_hub_default_db_url_is_sqlite_under_data_dir(tmp_path: Path) -> None:
    url = HubConfig.default_db_url(tmp_path)
    assert url.startswith("sqlite:///")
    assert url.endswith("data/hub.db")


@pytest.mark.unit
def test_runner_default_db_url_is_sqlite_under_data_dir(tmp_path: Path) -> None:
    url = RunnerConfig.default_db_url(tmp_path)
    assert url.startswith("sqlite:///")
    assert url.endswith("data/runner.db")


@pytest.mark.unit
def test_postgres_url_round_trips_through_config(tmp_path: Path) -> None:
    # A postgres URL is accepted verbatim — portability is a config value, not a code branch.
    pg = "postgresql+psycopg://blizzard:secret@localhost:5432/hub"
    root = tmp_path / "hub"
    root.mkdir()
    (root / "blizzard-hub.toml").write_text(f'db_url = "{pg}"\nhost = "0.0.0.0"\nport = 9001\n')
    config = HubConfig.load(root)
    assert config.db_url == pg
    assert config.host == "0.0.0.0"
    assert config.port == 9001


@pytest.mark.unit
def test_service_band_port_env_overrides_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(HUB_ENV_PORT, "4422")
    monkeypatch.setenv(RUNNER_ENV_PORT, "4423")
    assert HubConfig.scaffold(tmp_path).port == 4422
    assert RunnerConfig.scaffold(tmp_path).port == 4423


@pytest.mark.unit
def test_runner_loop_seams_scaffold_from_the_winter_injected_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The winter-service runner slot injects the loop seams so `blizzard runner init`
    # scaffolds a runnable config without hand-editing the toml.
    monkeypatch.setenv("BZ_WORKSPACE_ROOT", "/tmp/fixture/workspace")
    monkeypatch.setenv("BZ_WORKSPACE_ENVS", "e1, e2 ,e3")
    monkeypatch.setenv("BZ_HARNESS_BINARY", "/opt/mock-claude-code")
    monkeypatch.setenv("BZ_BASE_BRANCH", "main")
    config = RunnerConfig.scaffold(tmp_path)
    assert config.workspace_root == "/tmp/fixture/workspace"
    assert config.workspace_envs == ("e1", "e2", "e3")
    assert config.harness_binary == "/opt/mock-claude-code"
    assert config.base_branch == "main"


@pytest.mark.unit
def test_runner_loop_seams_fall_back_to_defaults_without_env(tmp_path: Path) -> None:
    config = RunnerConfig.scaffold(tmp_path)
    assert config.workspace_root == ""
    assert config.workspace_envs == ("e1",)
    assert config.harness_binary == "claude"
