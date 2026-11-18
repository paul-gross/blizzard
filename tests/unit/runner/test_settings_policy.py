"""The runner's settings policy names the reason a key cannot be written."""

import pytest

from blizzard.foundation.config_keys import STRING, ConfigKey, ConfigRegistry, Lifetime
from blizzard.runner.config_keys import HOST_REGISTRY
from blizzard.runner.settings.policy import SettingRefusal, check_keys


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
