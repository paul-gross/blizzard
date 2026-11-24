"""Resolve durable settings against the host's captured boot inputs."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from blizzard.foundation.config_keys import TABLE, ConfigKey, ConfigRegistry, ConfigView, InvalidConfigValue, Lifetime
from blizzard.foundation.flags import FlagRegistry
from blizzard.foundation.roles import domain_model
from blizzard.foundation.settings_record.vocabulary import Entry
from blizzard.hub.config import ConfigError, Env, HubConfig
from blizzard.hub.settings.values import thaw


def settable_declarations(registry: ConfigRegistry) -> tuple[ConfigKey, ...]:
    """The declarations of every live, settable leaf key."""
    return tuple(
        key
        for key in registry.keys
        if key.lifetime == Lifetime.PASS and not key.deprecated and "[]" not in key.path and key.type != TABLE
    )


def setting_keys(registry: ConfigRegistry) -> tuple[str, ...]:
    return tuple(key.path for key in settable_declarations(registry))


class SettingUnknown(Exception):
    """A key the hub does not declare as a settable setting."""

    def __init__(self, key: str) -> None:
        super().__init__(f"Unknown hub setting {key!r}")
        self.key = key


class SettingNotLive(Exception):
    """A declared boot setting: edited in the hub's config file, applied on restart."""

    def __init__(self, key: str) -> None:
        super().__init__(f"{key} is a boot setting; edit blizzard-hub.toml and restart")
        self.key = key


def require_settable(registry: ConfigRegistry, keys: Iterable[str]) -> None:
    """Refuse the first key, in sorted order, that is not a live setting."""
    settable = set(setting_keys(registry))
    for key in sorted(set(keys)):
        if key in settable:
            continue
        declaration = registry.key(key)
        if (
            declaration is not None
            and not declaration.deprecated
            and declaration.type != TABLE
            and "[]" not in key
            and declaration.lifetime != Lifetime.PASS
        ):
            raise SettingNotLive(key)
        raise SettingUnknown(key)


def _put(document: dict[str, object], path: str, value: object) -> None:
    parts = path.split(".")
    table = document
    for part in parts[:-1]:
        child = table.setdefault(part, {})
        assert isinstance(child, dict)
        table = cast(dict[str, object], child)
    table[parts[-1]] = thaw(value)


def export_document(registry: ConfigRegistry, entries: Mapping[str, Entry]) -> dict[str, object]:
    """Every settable key whose entry is off its default, as the nested document apply accepts."""
    settable = {key.path for key in settable_declarations(registry)}
    document: dict[str, object] = {}
    for path, entry in entries.items():
        if path in settable and not entry.at_default:
            _put(document, path, entry.value)
    return document


def _finite(value: object) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, Mapping):
        return all(_finite(item) for item in cast(Mapping[object, object], value).values())
    if isinstance(value, (list, tuple)):
        return all(_finite(item) for item in cast(list[object] | tuple[object, ...], value))
    return True


@domain_model
@dataclass(frozen=True)
class HubSettingsFooting:
    root: Path
    host: str | None
    port: int | None
    allow_external_db: bool
    flag_registry: FlagRegistry
    strict: bool
    key_registry: ConfigRegistry
    environ: Mapping[str, str]
    store_url: str | None = None
    document: Mapping[str, object] | None = None

    def resolve(self, entries: Mapping[str, Entry]) -> HubConfig:
        allowed = set(setting_keys(self.key_registry))
        document: dict[str, object] = deepcopy(dict(self.document or {}))
        for key in allowed:
            parts = key.split(".")
            table = document
            for part in parts[:-1]:
                child = table.get(part)
                if not isinstance(child, dict):
                    break
                table = cast(dict[str, object], child)
            else:
                table.pop(parts[-1], None)
        for key, entry in entries.items():
            if key not in allowed:
                declared = self.key_registry.key(key)
                if declared is not None and declared.lifetime == Lifetime.BOOT:
                    continue
                raise ConfigError(f"unknown hub setting {key!r}")
            if not entry.at_default:
                if not _finite(entry.value):
                    raise ConfigError(f"{key} must not contain a non-finite number")
                _put(document, key, entry.value)
        try:
            resolved = HubConfig.resolve_document(
                self.root,
                document,
                host=self.host,
                port=self.port,
                allow_external_db=self.allow_external_db,
                flag_registry=self.flag_registry,
                strict=self.strict,
                key_registry=self.key_registry,
                env=Env(self.environ),
            )
            if self.store_url is not None and resolved.db_url != self.store_url:
                raise ConfigError("db_url must continue to identify the store opened at boot")
            return resolved
        except InvalidConfigValue as exc:
            raise ConfigError(str(exc)) from exc

    def validate(self, entries: Mapping[str, Entry]) -> Mapping[str, str]:
        try:
            self.resolve(entries)
        except (ConfigError, ValueError, TypeError) as exc:
            return {"config": str(exc)}
        return {}

    def file_view(self, document: Mapping[str, object]) -> ConfigView:
        return ConfigView.root(self.key_registry, document, strict=self.strict)
