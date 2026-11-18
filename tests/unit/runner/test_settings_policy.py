"""The runner's settings policy names the reason a key cannot be written."""

import pytest

from blizzard.foundation.config_keys import STRING, ConfigKey, ConfigRegistry, Lifetime
from blizzard.runner.config_keys import HOST_REGISTRY
from blizzard.runner.settings.policy import SettingRefusal, check_host_keys, check_keys


def test_live_and_unknown_keys() -> None:
    check_keys(HOST_REGISTRY, ["agents", "projects"])
    for key in ("no-such-key", "projects.agents"):
        with pytest.raises(SettingRefusal) as refused:
            check_keys(HOST_REGISTRY, [key])
        assert refused.value.code == "setting_unknown"
        assert refused.value.key == key


def test_boot_and_immutable_have_distinct_refusals() -> None:
    registry = ConfigRegistry(
        "test",
        (
            ConfigKey("runner[].boot", STRING, lifetime=Lifetime.BOOT),
            ConfigKey("runner[].identity", STRING, lifetime=Lifetime.IMMUTABLE),
        ),
    )
    with pytest.raises(SettingRefusal) as boot:
        check_keys(registry, ["boot"])
    assert boot.value.code == "setting_not_live"
    assert "blizzard-host.toml" in str(boot.value) and "restart" in str(boot.value)
    with pytest.raises(SettingRefusal) as immutable:
        check_keys(registry, ["identity"])
    assert immutable.value.code == "setting_immutable"


def test_host_keys_refuse_boot_runner_and_unknown_keys() -> None:
    check_host_keys(HOST_REGISTRY, ["tick_seconds", "tracing.sweep_seconds", "tools.path_prepend"])
    for key, code in (
        ("port", "setting_not_live"),
        ("tracing.platform", "setting_not_live"),
        ("agents", "setting_unknown"),
        ("runner[].agents", "setting_unknown"),
        ("tracing", "setting_unknown"),
        ("no-such-key", "setting_unknown"),
    ):
        with pytest.raises(SettingRefusal) as refused:
            check_host_keys(HOST_REGISTRY, [key])
        assert (refused.value.code, refused.value.key) == (code, key)


def test_host_immutable_keys_have_their_own_refusal() -> None:
    registry = ConfigRegistry("test", (ConfigKey("identity", STRING, lifetime=Lifetime.IMMUTABLE),))
    with pytest.raises(SettingRefusal) as immutable:
        check_host_keys(registry, ["identity"])
    assert immutable.value.code == "setting_immutable"
