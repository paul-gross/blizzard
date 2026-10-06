"""A hub refuses to start while a legacy key remains, and a hub without any starts empty (component tier)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from blizzard.hub import app as hub_app
from blizzard.hub import runtime as hub_runtime
from blizzard.hub.config import RESERVED_HUB_SOURCE_NAME, ConfigError, HubConfig
from blizzard.hub.secrets import ENV_SECRET_KEY

pytestmark = pytest.mark.component

_TOKEN_ENV = "BZ_WORK_SOURCE_TOKEN"
_LEGACY_BLOCK = f"""
[[work_source]]
name = "blizzard"
provider = "github"
repo = "paul-gross/blizzard"
token_env = "{_TOKEN_ENV}"
"""
_SECRET_VALUE = "legacy-token-value"


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> HubConfig:
    monkeypatch.delenv(ENV_SECRET_KEY, raising=False)
    monkeypatch.delenv(_TOKEN_ENV, raising=False)
    return hub_runtime.init_environment(tmp_path / "hub")


def _start(config: HubConfig) -> None:
    app = hub_app.build_hosted_app(HubConfig.load(config.root))
    app.state.engine.dispose()


def _add_legacy_keys(config: HubConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    config.config_path.write_text(config.config_path.read_text() + _LEGACY_BLOCK)
    monkeypatch.setenv(_TOKEN_ENV, _SECRET_VALUE)
    monkeypatch.setenv("BZ_FORGE_URL", "http://forge.invalid")


def test_legacy_keys_without_an_import_refuse_naming_the_import_command(
    hub: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_legacy_keys(hub, monkeypatch)

    with pytest.raises(ConfigError) as refused:
        _start(hub)

    assert f"blizzard hub config import-legacy --dir {hub.root}" in str(refused.value)
    assert _SECRET_VALUE not in str(refused.value)


def test_legacy_keys_left_after_an_import_refuse_naming_each_key_and_where(
    hub: HubConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _start(hub)  # mints the hub key the import requires
    _add_legacy_keys(hub, monkeypatch)
    hub_app.import_legacy_config(HubConfig.load(hub.root), dict(os.environ))

    with pytest.raises(ConfigError) as refused:
        _start(hub)

    message = str(refused.value)
    assert f'{hub.config_path.name} [[work_source]] "blizzard"' in message
    assert f"environment {_TOKEN_ENV}" in message
    assert "environment BZ_FORGE_URL" in message
    assert "import-legacy" not in message
    assert _SECRET_VALUE not in message
    assert "http://forge.invalid" not in message


def test_an_imported_hub_with_its_legacy_keys_removed_starts(hub: HubConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    _start(hub)
    original = hub.config_path.read_text()
    _add_legacy_keys(hub, monkeypatch)
    hub_app.import_legacy_config(HubConfig.load(hub.root), dict(os.environ))
    hub.config_path.write_text(original)
    monkeypatch.delenv(_TOKEN_ENV)
    monkeypatch.delenv("BZ_FORGE_URL")

    app = hub_app.build_hosted_app(HubConfig.load(hub.root))
    try:
        assert sorted(app.state.services.work_sources.names()) == sorted([RESERVED_HUB_SOURCE_NAME, "blizzard"])
    finally:
        app.state.engine.dispose()


def test_a_fresh_hub_without_legacy_keys_starts_with_an_empty_store(hub: HubConfig) -> None:
    app = hub_app.build_hosted_app(HubConfig.load(hub.root))
    try:
        services = app.state.services
        assert services.work_sources.names() == [RESERVED_HUB_SOURCE_NAME]
        assert services.work_source_records.list_all(include_retired=True) == []
        assert services.repository_records.list_all(include_retired=True) == []
    finally:
        app.state.engine.dispose()
