"""Registry policy for runner settings authoring, independent of transport."""

from __future__ import annotations

from collections.abc import Iterable

from blizzard.foundation.config_keys import ConfigRegistry, Lifetime
from blizzard.runner.settings.keys import host_keys, runner_keys


class SettingRefusal(ValueError):
    """A key cannot be authored through the runner settings record."""

    def __init__(self, code: str, key: str, message: str) -> None:
        self.code = code
        self.key = key
        super().__init__(message)


def check_keys(registry: ConfigRegistry, keys: Iterable[str]) -> None:
    """Refuse non-live keys; nested array members are not whole record entries."""
    writable = runner_keys(registry)
    declarations = {
        item.path.removeprefix("runner[]."): item for item in registry.keys if item.path.startswith("runner[].")
    }
    for key in sorted(set(keys)):
        if key in writable:
            continue
        declaration = declarations.get(key)
        if declaration is not None and declaration.lifetime == Lifetime.BOOT:
            raise SettingRefusal(
                "setting_not_live", key, f"{key} must be changed in blizzard-host.toml and requires a restart"
            )
        if declaration is not None and declaration.lifetime == Lifetime.IMMUTABLE:
            raise SettingRefusal("setting_immutable", key, f"{key} is immutable")
        raise SettingRefusal("setting_unknown", key, f"unknown runner setting {key}")


def check_host_keys(registry: ConfigRegistry, keys: Iterable[str]) -> None:
    """Refuse every key the host record does not hold whole: a boot key, an immutable one, or one it never declares."""
    writable = host_keys(registry)
    declarations = {item.path: item for item in registry.keys if "[]" not in item.path and not item.deprecated}
    for key in sorted(set(keys)):
        if key in writable:
            continue
        declaration = declarations.get(key)
        if declaration is not None and declaration.lifetime == Lifetime.BOOT:
            raise SettingRefusal(
                "setting_not_live", key, f"{key} must be changed in blizzard-host.toml and requires a restart"
            )
        if declaration is not None and declaration.lifetime == Lifetime.IMMUTABLE:
            raise SettingRefusal("setting_immutable", key, f"{key} is immutable")
        raise SettingRefusal("setting_unknown", key, f"unknown host setting {key}")
