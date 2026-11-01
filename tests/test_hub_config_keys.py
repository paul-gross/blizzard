"""The hub's config registry against the loader: every declared key is read, an undeclared read raises when the load is
strict and is ignored when it is lenient."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from blizzard.foundation.config_keys import (
    OPEN_TABLE,
    ConfigRegistry,
    ConfigView,
    UndeclaredConfigKey,
)
from blizzard.hub import config as hub_config
from blizzard.hub.config import HubConfig
from blizzard.hub.config_keys import HUB_REGISTRY
from blizzard.hub.domain.sweeps.names import SweepName
from tests.config_inputs import load_hub_config
from tests.test_host_config_keys import _document_carrying_every_key

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
    **{f"sweeps.{name}.mode": "stepped" for name in SweepName},
}


def _initialised(root: Path) -> Path:
    (root / "data").mkdir(parents=True)
    (root / hub_config.CONFIG_FILENAME).write_text(HubConfig.scaffold(root).to_toml())
    return root


def test_every_declared_hub_key_is_read_by_a_load_that_carries_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    read: set[str] = set()
    checked = ConfigView._check

    def recording(view: ConfigView, key: str) -> None:
        read.add(view._join(key))
        checked(view, key)

    monkeypatch.setattr(ConfigView, "_check", recording)
    root = _initialised(tmp_path)
    document = _document_carrying_every_key(HUB_REGISTRY, _SAMPLES)
    # Replaces the module's parser alone: the flag registry is parsed with the real one.
    monkeypatch.setattr(hub_config, "tomllib", SimpleNamespace(loads=lambda _text: document))
    load_hub_config(root, allow_external_db=True)
    hub_config.read_legacy_keys(
        root / hub_config.CONFIG_FILENAME, {}, strict=True
    )  # [[work_source]] is read apart from the load

    unread = [key.path for key in HUB_REGISTRY.keys if key.type != OPEN_TABLE and key.path not in read]
    assert unread == []


def test_an_undeclared_hub_key_read_names_the_key_and_the_hub_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    without_port = ConfigRegistry("hub", tuple(key for key in HUB_REGISTRY.keys if key.path != "port"))
    monkeypatch.setattr(hub_config, "HUB_REGISTRY", without_port)
    with pytest.raises(UndeclaredConfigKey, match=r"'port'.*hub") as raised:
        load_hub_config(_initialised(tmp_path))
    assert (raised.value.path, raised.value.registry) == ("port", "hub")


def test_a_hub_file_carrying_an_undeclared_key_loads_when_read_leniently(tmp_path: Path) -> None:
    root = _initialised(tmp_path)
    path = root / hub_config.CONFIG_FILENAME
    path.write_text("undeclared_probe = 1\n" + path.read_text() + "\n[flags]\nprobe = true\n")
    assert load_hub_config(root, strict=False).root == root.resolve()
