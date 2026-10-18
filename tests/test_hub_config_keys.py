"""The hub's config registry against the loader: every declared key is read, an undeclared read raises under the
switch and is ignored without it."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.foundation.config_keys import (
    OPEN_TABLE,
    STRICT_CONFIG_ENVIRONMENT_VARIABLE,
    ConfigRegistry,
    ConfigView,
    UndeclaredConfigKey,
)
from blizzard.hub import config as hub_config
from blizzard.hub.config import HubConfig
from blizzard.hub.config_keys import HUB_REGISTRY
from tests.test_host_config_keys import _document_carrying_every_key  # pyright: ignore[reportPrivateUsage]

pytestmark = pytest.mark.unit

_SAMPLES: dict[str, object] = {
    "route_token_mode": "warn",
    "produces_mode": "warn",
    "auth.mode": "none",
    "work_source[].provider": "github",
    "egress.format": "ndjson",
    "egress.datasets": ["steps"],
    "egress.file_paths": "relative",
    "egress.extractor_versions": "current",
    "egress.path_key_env": "BZ_EGRESS_PATH_KEY",
    "public_url": "https://hub.example",
    "trusted_proxies": ["127.0.0.1"],
    "tracing.platform_sample_ratio": 0.5,
    "tracing.worker_programs": False,
}


def _initialised(root: Path) -> Path:
    (root / "data").mkdir(parents=True)
    (root / hub_config.CONFIG_FILENAME).write_text(HubConfig.scaffold(root).to_toml())
    return root


def test_every_declared_hub_key_is_read_by_a_load_that_carries_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    read: set[str] = set()
    checked = ConfigView._check  # pyright: ignore[reportPrivateUsage]

    def recording(view: ConfigView, key: str) -> None:
        read.add(view._join(key))  # pyright: ignore[reportPrivateUsage]
        checked(view, key)

    monkeypatch.setattr(ConfigView, "_check", recording)
    root = _initialised(tmp_path)
    document = _document_carrying_every_key(HUB_REGISTRY, _SAMPLES)
    monkeypatch.setattr(hub_config.tomllib, "loads", lambda _text: document)
    HubConfig.load(root, allow_external_db=True)
    hub_config.read_legacy_keys(root / hub_config.CONFIG_FILENAME, {})  # [[work_source]] is read apart from the load

    unread = [key.path for key in HUB_REGISTRY.keys if key.type != OPEN_TABLE and key.path not in read]
    assert unread == []


def test_an_undeclared_hub_key_read_names_the_key_and_the_hub_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    without_port = ConfigRegistry("hub", tuple(key for key in HUB_REGISTRY.keys if key.path != "port"))
    monkeypatch.setattr(hub_config, "HUB_REGISTRY", without_port)
    with pytest.raises(UndeclaredConfigKey, match=r"'port'.*hub") as raised:
        HubConfig.load(_initialised(tmp_path))
    assert (raised.value.path, raised.value.registry) == ("port", "hub")


def test_a_hub_file_carrying_an_undeclared_key_loads_when_the_switch_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(STRICT_CONFIG_ENVIRONMENT_VARIABLE)
    root = _initialised(tmp_path)
    path = root / hub_config.CONFIG_FILENAME
    path.write_text("undeclared_probe = 1\n" + path.read_text() + "\n[flags]\nprobe = true\n")
    assert HubConfig.load(root).root == root.resolve()
