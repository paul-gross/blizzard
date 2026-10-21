"""A configuration key a flag guards: its value is checked whatever the flag says, it reads as absent while the flag
is off — with a warning naming key and flag — and applies once on; an illegal flag pairing is refused at load
(``bzh:flag-hides-behavior``)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
import structlog

from blizzard.foundation.config_keys import ConfigKey, ConfigRegistry
from blizzard.foundation.flags import FlagRegistry
from blizzard.hub.composition import build_hub_flags
from blizzard.hub.config import ConfigError, HubConfig
from blizzard.hub.config_defaults import DEFAULT_ANNOTATION_INTERVAL_SECONDS
from blizzard.hub.config_keys import HUB_REGISTRY
from blizzard.hub.layout import CONFIG_FILENAME
from blizzard.runner.composition import build_host_process
from blizzard.runner.config import DEFAULT_HUB_URL
from blizzard.runner.config_keys import HOST_REGISTRY
from blizzard.runner.host_config import BuiltRunner, HostConfig, HostConfigError
from tests.config_inputs import load_host_config, load_hub_config
from tests.host_files import write_minimal_host

_FLAGS = FlagRegistry.parse(
    """
[flag.hub-thing]
owner = "epic:x"
summary = "A hub capability"
scope = "hub"
default = "off"

[flag.fleet-thing]
owner = "epic:x"
summary = "A fleet capability"
scope = "fleet"
default = "off"

[flag.runner-thing]
owner = "epic:x"
summary = "A runner capability"
scope = "runner"
default = "off"
""",
    "fixture",
)


def _guard(registry: ConfigRegistry, path: str, flag: str) -> ConfigRegistry:
    return replace(registry, keys=tuple(replace(k, flag=flag) if k.path == path else k for k in registry.keys))


_HUB_KEYS = _guard(HUB_REGISTRY, "annotation_interval_seconds", "hub-thing")
_HOST_KEYS = _guard(HOST_REGISTRY, "hub_url", "runner-thing")


def _hub(tmp_path: Path, text: str, keys: ConfigRegistry = _HUB_KEYS) -> HubConfig:
    (tmp_path / CONFIG_FILENAME).write_text(text)
    return load_hub_config(tmp_path, flag_registry=_FLAGS, key_registry=keys)


def _host(tmp_path: Path, top: str, keys: ConfigRegistry = _HOST_KEYS) -> HostConfig:
    write_minimal_host(tmp_path, top=top)
    return load_host_config(tmp_path, flag_registry=_FLAGS, key_registry=keys)


@pytest.mark.unit
def test_a_hub_key_with_its_flag_off_loads_as_its_default_and_composition_warns_naming_key_and_flag(
    tmp_path: Path,
) -> None:
    config = _hub(tmp_path, "annotation_interval_seconds = 99\n")
    assert config.annotation_interval_seconds == DEFAULT_ANNOTATION_INTERVAL_SECONDS
    assert [(h.key, h.flag) for h in config.held_keys] == [("annotation_interval_seconds", "hub-thing")]
    with structlog.testing.capture_logs() as logs:
        flags = build_hub_flags(config)
    assert flags.on("hub-thing") is False
    warned = [e for e in logs if e["log_level"] == "warning"]
    assert [(e["key"], e["flag"]) for e in warned] == [("annotation_interval_seconds", "hub-thing")]


@pytest.mark.unit
def test_a_loaded_config_carries_the_registry_and_resolution_composition_applies(tmp_path: Path) -> None:
    hub = _hub(tmp_path, "[flags]\nhub-thing = true\n")
    assert hub.flag_registry is _FLAGS
    assert hub.flag_resolution is not None and hub.flag_resolution.flags.on("hub-thing")
    assert build_hub_flags(hub).on("hub-thing") is True
    host = _host(tmp_path, "[flags]\nrunner-thing = false\n").host_view()
    assert host.flag_registry is _FLAGS
    assert host.flag_resolution is not None and host.flag_resolution.flags.on("runner-thing") is False


@pytest.mark.unit
def test_a_hub_key_with_its_flag_on_applies_and_an_absent_one_is_not_held(tmp_path: Path) -> None:
    on = _hub(tmp_path, "annotation_interval_seconds = 99\n[flags]\nhub-thing = true\n")
    assert (on.annotation_interval_seconds, on.held_keys) == (99, ())
    absent = _hub(tmp_path, "")
    assert (absent.annotation_interval_seconds, absent.held_keys) == (DEFAULT_ANNOTATION_INTERVAL_SECONDS, ())


@pytest.mark.unit
@pytest.mark.parametrize("flags", ["", "[flags]\nhub-thing = true\n"])
def test_a_malformed_hub_value_fails_the_load_whatever_the_flag_says(tmp_path: Path, flags: str) -> None:
    with pytest.raises(ConfigError, match="annotation_interval_seconds must be a integer"):
        _hub(tmp_path, f'annotation_interval_seconds = "soon"\n{flags}')


@pytest.mark.unit
def test_a_host_key_with_its_flag_off_loads_as_its_default_with_a_warning_and_composition_warns(
    tmp_path: Path,
) -> None:
    config = _host(tmp_path, 'hub_url = "http://elsewhere:1"\n')
    assert config.hub_url == DEFAULT_HUB_URL
    assert config.warnings == ("hub_url is ignored while the flag runner-thing is off",)
    assert [(h.key, h.flag) for h in config.held_keys] == [("hub_url", "runner-thing")]
    built = [p for p in config.projections({"BZ_HUB_TOKEN_RUNNER_LOCAL": "tok"}) if isinstance(p, BuiltRunner)]
    with structlog.testing.capture_logs() as logs:
        host = build_host_process(config, built)
    try:
        warned = [e for e in logs if e.get("event") == "config key ignored: its flag is off"]
        assert [(e["key"], e["flag"]) for e in warned] == [("hub_url", "runner-thing")]
    finally:
        host.close()


@pytest.mark.unit
def test_a_host_key_with_its_flag_on_applies_and_a_malformed_value_fails_the_load(tmp_path: Path) -> None:
    on = _host(tmp_path, 'hub_url = "http://elsewhere:1"\n[flags]\nrunner-thing = true\n')
    assert (on.hub_url, on.held_keys) == ("http://elsewhere:1", ())
    with pytest.raises(HostConfigError, match="hub_url must be a string"):
        _host(tmp_path, "hub_url = 3\n")


@pytest.mark.unit
@pytest.mark.parametrize("flag", ["runner-thing", "mistyped"])
def test_a_hub_key_naming_a_flag_a_hub_cannot_read_at_load_is_refused(tmp_path: Path, flag: str) -> None:
    with pytest.raises(ValueError, match="flag"):
        _hub(tmp_path, "", _guard(HUB_REGISTRY, "annotation_interval_seconds", flag))


@pytest.mark.unit
@pytest.mark.parametrize("flag", ["hub-thing", "fleet-thing", "mistyped"])
def test_a_host_key_naming_anything_but_a_runner_flag_is_refused(tmp_path: Path, flag: str) -> None:
    write_minimal_host(tmp_path)
    with pytest.raises(HostConfigError, match="flag"):
        load_host_config(tmp_path, flag_registry=_FLAGS, key_registry=_guard(HOST_REGISTRY, "hub_url", flag))


@pytest.mark.unit
def test_a_hub_key_may_name_a_fleet_flag(tmp_path: Path) -> None:
    keys = _guard(HUB_REGISTRY, "annotation_interval_seconds", "fleet-thing")
    assert _hub(tmp_path, "annotation_interval_seconds = 99\n", keys).held_keys[0].flag == "fleet-thing"


@pytest.mark.unit
def test_the_exported_contract_leaves_the_flag_out() -> None:
    from blizzard.tools.config_contract import document_from_registry

    document = document_from_registry(_HUB_KEYS)
    assert all("flag" not in key for key in document["keys"])


@pytest.mark.unit
def test_config_key_is_still_the_declaration_type() -> None:
    assert ConfigKey("k", "string", flag="hub-thing").flag == "hub-thing"


@pytest.mark.unit
def test_a_table_key_cannot_be_guarded(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot be guarded"):
        _hub(tmp_path, "", _guard(HUB_REGISTRY, "transcripts", "hub-thing"))
