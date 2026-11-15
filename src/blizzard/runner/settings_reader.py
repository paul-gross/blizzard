"""Resolve a runner's stored entries through the host's existing parser."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any, Protocol, cast

from blizzard.foundation.config_keys import ConfigView
from blizzard.foundation.settings_record.vocabulary import Entry
from blizzard.runner.config import RunnerConfig
from blizzard.runner.config_keys import HOST_REGISTRY
from blizzard.runner.config_table import ConfigError
from blizzard.runner.host_config import HostConfig, RunnerEntry, parse_runner_entry
from blizzard.runner.settings.keys import runner_keys
from blizzard.runner.settings.ports import SettingsSnapshot


class SettingsSnapshotSource(Protocol):
    def snapshot(self) -> SettingsSnapshot: ...


class RunnerSettingsReader:
    """Materialize one coherent record with boot-held file inputs and parser defaults."""

    def __init__(self, host: HostConfig, entry: RunnerEntry, store: SettingsSnapshotSource) -> None:
        self.host = host
        self.entry = entry
        self.store = store
        self.keys = runner_keys(HOST_REGISTRY)

    def snapshot(self) -> tuple[SettingsSnapshot, RunnerConfig]:
        snapshot = self.store.snapshot()
        return snapshot, self.resolve(snapshot.entries)

    def preview_seed(self) -> RunnerConfig:
        """Resolve the entries a first seed would produce, without writing them."""
        entries = dict(self.store.snapshot().entries)
        authored = self.entry.authored
        assert authored is not None
        entries.update(
            {key: Entry(authored.value(key)) for key in self.keys if key not in entries and authored.states(key)}
        )
        return self.resolve(entries)

    def resolve(self, entries: Mapping[str, Entry]) -> RunnerConfig:
        entry, errors = self._resolve(entries)
        if errors or entry.own is None:
            raise ValueError("; ".join(errors))
        return self.host.config_for_runner(entry, entry.own, self.host.token_for(self.entry))

    def validate(self, entries: Mapping[str, Entry]) -> Mapping[str, str]:
        unknown = {key: "not a writable runner setting" for key in entries if key not in self.keys}
        if unknown:
            return unknown
        _, errors = self._resolve(entries)
        return {"runner": "; ".join(errors)} if errors else {}

    def _resolve(self, entries: Mapping[str, Entry]) -> tuple[RunnerEntry, list[str]]:
        authored = self.entry.authored or ConfigView.unchecked({})
        body = deepcopy(dict(authored.body))
        for key in self.keys:
            parts = key.split(".")
            table = body
            parents: list[tuple[dict[str, Any], str]] = []
            for part in parts[:-1]:
                value = table.get(part)
                if not isinstance(value, dict):
                    break
                parents.append((table, part))
                table = cast(dict[str, Any], value)
            else:
                if parts[-1] in table:
                    del table[parts[-1]]
                    for parent, part in reversed(parents):
                        if parent[part]:
                            break
                        del parent[part]
        for key, value in entries.items():
            if key not in self.keys or value.at_default:
                continue
            parts = key.split(".")
            table = body
            for part in parts[:-1]:
                table = table.setdefault(part, {})
            table[parts[-1]] = deepcopy(value.value)
        view = ConfigView(authored.registry, authored.path, body, authored.checked, authored.strict, authored.flags)
        # The file's name identifies the boot entry (and its token); a stored
        # display name may override it, but an unset resolves to that identity.
        name = body.get("name", self.entry.name)
        if not isinstance(name, str) or not name.strip():
            return self.entry, ["name must be a non-blank string"]
        body["name"] = name
        try:
            parsed = parse_runner_entry(
                view, name.strip(), root=self.host.root, db_url=self.host.db_url, path=self.host.config_path
            )
            return parsed, self.host.runner_problems(parsed)
        except (ConfigError, ValueError, TypeError) as exc:
            return self.entry, [str(exc)]
