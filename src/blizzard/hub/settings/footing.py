"""Resolve durable settings against the host's captured boot inputs."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from blizzard.foundation.config_keys import TABLE, ConfigRegistry, ConfigView, InvalidConfigValue, Lifetime
from blizzard.foundation.flags import FlagRegistry
from blizzard.foundation.roles import domain_model
from blizzard.foundation.settings_record.vocabulary import Entry
from blizzard.hub.config import ConfigError, Env, HubConfig
from blizzard.hub.settings.values import thaw


def setting_keys(registry: ConfigRegistry) -> tuple[str, ...]:
    return tuple(
        key.path
        for key in registry.keys
        if key.lifetime == Lifetime.PASS and not key.deprecated and "[]" not in key.path and key.type != TABLE
    )


def _put(document: dict[str, object], path: str, value: object) -> None:
    parts = path.split(".")
    table = document
    for part in parts[:-1]:
        child = table.setdefault(part, {})
        assert isinstance(child, dict)
        table = cast(dict[str, object], child)
    table[parts[-1]] = thaw(value)


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
