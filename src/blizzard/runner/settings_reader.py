"""Resolve a runner's stored entries through the host's existing parser."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any, Protocol, cast

from blizzard.foundation.config_keys import ConfigView
from blizzard.foundation.settings_record.vocabulary import Entry
from blizzard.runner.config import RunnerConfig
from blizzard.runner.config_keys import HOST_REGISTRY
from blizzard.runner.config_table import ConfigError
from blizzard.runner.host_config import HostConfig, RunnerEntry, parse_runner_entry
from blizzard.runner.settings.keys import runner_keys
from blizzard.runner.settings.ports import IWriteRunnerSettings, SettingsSnapshot


class SettingsSnapshotSource(Protocol):
    def snapshot(self) -> SettingsSnapshot: ...


class RunnerSettingsReader:
    """Materialize one coherent record with boot-held file inputs and parser defaults."""

    def __init__(
        self,
        host: HostConfig,
        entry: RunnerEntry,
        store: SettingsSnapshotSource,
        *,
        deferred_seed: IWriteRunnerSettings | None = None,
        identity_present: Callable[[], bool] | None = None,
    ) -> None:
        self.host = host
        self.entry = entry
        self.store = store
        self._deferred_seed = deferred_seed
        self._identity_present = identity_present
        self.keys = runner_keys(HOST_REGISTRY)
        prefix = "runner[]."
        self.aliases = {
            key.path.removeprefix(prefix): key.replaced_by.removeprefix(prefix)
            for key in HOST_REGISTRY.keys
            if key.deprecated
            and key.path.startswith(prefix)
            and key.replaced_by is not None
            and key.replaced_by.startswith(prefix)
            and "[]" not in key.path.removeprefix(prefix)
            and key.replaced_by.removeprefix(prefix) in self.keys
        }

    def seed_keys(self, entries: Mapping[str, Entry]) -> tuple[str, ...]:
        # An alias and its replacement represent one authored setting. Once either
        # has an entry, a later file edit must not seed the other spelling.
        blocked = {alias for alias, key in self.aliases.items() if key in entries}
        blocked.update(key for alias, key in self.aliases.items() if alias in entries)
        return tuple(key for key in (*self.keys, *self.aliases) if key not in blocked)

    def snapshot(self) -> tuple[SettingsSnapshot, RunnerConfig]:
        seed = self._deferred_seed
        if seed is not None and self._identity_present is not None:
            if not self._identity_present():
                snapshot = self.store.snapshot()
                return snapshot, self._preview_entries(snapshot.entries)
            assert self.entry.authored is not None
            seed.seed(self.entry.authored, (*self.keys, *self.aliases), validate=self.validate, aliases=self.aliases)
            self._deferred_seed = None
        snapshot = self.store.snapshot()
        return snapshot, self.resolve(snapshot.entries)

    def preview_seed(self) -> RunnerConfig:
        """Resolve the entries a first seed would produce, without writing them."""
        return self._preview_entries(self.store.snapshot().entries)

    def resolve_for_read(self, entries: Mapping[str, Entry]) -> RunnerConfig:
        """Show authored inputs until registration makes a durable seed possible."""
        if self._deferred_seed is not None:
            return self._preview_entries(entries)
        return self.resolve(entries)

    def _preview_entries(self, current: Mapping[str, Entry]) -> RunnerConfig:
        entries = dict(current)
        authored = self.entry.authored
        assert authored is not None
        entries.update(
            {
                key: Entry(authored.value(key))
                for key in self.seed_keys(entries)
                if key not in entries and authored.states(key)
            }
        )
        return self.resolve(entries)

    def resolve(self, entries: Mapping[str, Entry]) -> RunnerConfig:
        entry, errors = self._resolve(entries)
        if errors or entry.own is None:
            raise ValueError("; ".join(errors))
        return self.host.config_for_runner(entry, entry.own, self.host.token_for(self.entry))

    def validate(self, entries: Mapping[str, Entry]) -> Mapping[str, str]:
        unknown = {
            key: "not a writable runner setting" for key in entries if key not in self.keys and key not in self.aliases
        }
        if unknown:
            return unknown
        _, errors = self._resolve(entries)
        result: dict[str, str] = {}
        for error in errors:
            for key in self._error_keys(error, entries):
                result[key] = f"{result[key]}; {error}" if key in result else error
        return result

    def _error_keys(self, error: str, entries: Mapping[str, Entry]) -> set[str]:
        """Translate parser diagnostics to record keys, retaining whole-array ownership.

        Parser diagnostics are also shown at boot and are not a structured API. Prefer
        explicit paths, and conservatively attribute an unrecognised diagnostic to the
        supplied entries rather than inventing a setting called ``runner``.
        """
        diagnostic = re.sub(r" \(in .+\)$", "", error)
        text = re.sub(r"\[([a-z_]+)\]\s*\.?\s*", r"\1.", diagnostic)
        keys = {*self.keys, *self.aliases}
        found = {key for key in keys if re.search(rf"(?<![\w.]){re.escape(key)}(?![\w])", text)}
        if "runner_ceiling_usd" in error:
            found.add("cost.runner_ceiling_usd")
            found.update(key for key in ("cost.window_hours", "throttle.budget.window_hours") if key in entries)
        if "has no agents" in error:
            found.add("agents")
        if "has no projects" in error or error.startswith(("binding of project ", "binds project ")):
            found.add("projects")
        if error.startswith("names subscription "):
            found.add("subscription")
        if not found:
            # A table-level diagnostic may implicate several whole settings.
            for key in keys:
                parent = key.rpartition(".")[0]
                if parent and re.search(rf"(?<![\w.]){re.escape(parent)}(?![\w.])", text):
                    found.add(key)
        return found or set(entries) or {"name"}

    def _resolve(self, entries: Mapping[str, Entry]) -> tuple[RunnerEntry, list[str]]:
        authored = self.entry.authored or ConfigView.unchecked({})
        body = deepcopy(dict(authored.body))
        for key in (*self.keys, *self.aliases):
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
            if (key not in self.keys and key not in self.aliases) or value.at_default:
                continue
            if key in self.aliases and self.aliases[key] in entries:
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
