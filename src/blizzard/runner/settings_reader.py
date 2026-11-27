"""Resolve a runner's stored entries through the host's existing parser."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal, cast

from blizzard.foundation.config_keys import ConfigView, Lifetime
from blizzard.foundation.roles import domain_model
from blizzard.foundation.settings_record.vocabulary import Entry
from blizzard.runner.config import RunnerConfig
from blizzard.runner.config_keys import HOST_REGISTRY
from blizzard.runner.config_table import ConfigError
from blizzard.runner.host_config import HostConfig, RunnerEntry, parse_runner_entry
from blizzard.runner.host_records.model import HostRecord, HostRecordsSnapshot, RecordKind
from blizzard.runner.host_settings_reader import HostSettingsReader
from blizzard.runner.settings.keys import runner_keys
from blizzard.runner.settings.ports import (
    IJudgeHostApply,
    IReadRunnerSettings,
    IWriteRunnerSettings,
    SettingsChange,
    SettingsPair,
    SettingsSnapshot,
)


@domain_model
@dataclass(frozen=True)
class EffectiveRunnerSetting:
    """A setting's selected record entry and the origin of its displayed value."""

    lifetime: Lifetime
    entry: Entry | None
    provenance_key: str
    source: Literal["entry", "default"]
    value_from: Literal["entry", "resolved", "identity"]
    identity_value: object = None


@domain_model
@dataclass(frozen=True)
class RunnerAlias:
    """A legacy spelling and whether its entry substitutes for the replacement on reads."""

    replacement: str
    substitutes: bool = True


# Permission mode is a separate harness override, not a value of autonomy.
_ALIAS_SUBSTITUTION = {"harness_permission_mode": False}


class RunnerEntriesJudge:
    """Judges a runner's settings entries laid over its file entry — reading no stored record, so a runner with none
    yet is judged the same as one with its record."""

    def __init__(self, host: HostConfig, entry: RunnerEntry) -> None:
        self.host = host
        self.entry = entry
        self.keys = runner_keys(HOST_REGISTRY)
        prefix = "runner[]."
        self.alias_metadata = {
            key.path.removeprefix(prefix): RunnerAlias(
                key.replaced_by.removeprefix(prefix), _ALIAS_SUBSTITUTION.get(key.path.removeprefix(prefix), True)
            )
            for key in HOST_REGISTRY.keys
            if key.deprecated
            and key.path.startswith(prefix)
            and key.replaced_by is not None
            and key.replaced_by.startswith(prefix)
            and "[]" not in key.path.removeprefix(prefix)
            and key.replaced_by.removeprefix(prefix) in self.keys
        }
        self.aliases = {alias: metadata.replacement for alias, metadata in self.alias_metadata.items()}

    def effective(self, snapshot: SettingsSnapshot) -> Mapping[str, EffectiveRunnerSetting]:
        """Select visible settings and their record provenance from one snapshot."""
        keys = dict(self.keys)
        keys.update(
            {
                key.path.removeprefix("runner[]."): key.lifetime
                for key in HOST_REGISTRY.keys
                if key.path.startswith("runner[].") and key.lifetime == Lifetime.IMMUTABLE and not key.deprecated
            }
        )
        result: dict[str, EffectiveRunnerSetting] = {}
        for key, lifetime in keys.items():
            selected = key
            entry = snapshot.entries.get(key)
            if entry is None:
                for alias, metadata in self.alias_metadata.items():
                    if metadata.replacement == key and metadata.substitutes and alias in snapshot.entries:
                        selected = alias
                        entry = snapshot.entries[alias]
                        break
            source: Literal["entry", "default"] = "entry" if entry is not None and not entry.at_default else "default"
            value_from: Literal["entry", "resolved", "identity"] = (
                "entry" if source == "entry" and selected == key else "resolved"
            )
            identity_value: object = None
            if key == "id":
                value_from, identity_value = "identity", self.entry.id
            elif key == "name" and source == "default":
                value_from, identity_value = "identity", self.entry.name
            result[key] = EffectiveRunnerSetting(lifetime, entry, selected, source, value_from, identity_value)
        return result

    def validate(self, entries: Mapping[str, Entry]) -> Mapping[str, str]:
        """A write's judgement: every error the entries resolve to, and each binding or subscription they name that
        the host does not serve — so a runner left bound to a retired workspace has every write refused until one
        fixes the binding."""
        return self._judge(entries, serving=True)

    def seed_validate(self, entries: Mapping[str, Entry]) -> Mapping[str, str]:
        """A seed's judgement: the errors that would leave the runner unbuilt, never a binding or subscription the
        host does not serve, which halts no runner."""
        return self._judge(entries, serving=False)

    def _judging_host(self) -> HostConfig:
        """The host a write's bindings and subscription are judged against."""
        return self.host

    def _judge(self, entries: Mapping[str, Entry], *, serving: bool) -> Mapping[str, str]:
        unknown = {
            key: "not a writable runner setting" for key in entries if key not in self.keys and key not in self.aliases
        }
        if unknown:
            return unknown
        entry, errors = self._resolve(entries)
        if serving and entry is not self.entry:
            # Only a parse that produced the candidate entry names the bindings it is judged by.
            errors = [*errors, *self._judging_host().binding_problems(entry)]
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


class RunnerSettingsReader(RunnerEntriesJudge):
    """Materialize one coherent record with boot-held file inputs and parser defaults."""

    def __init__(
        self,
        host: HostConfig,
        entry: RunnerEntry,
        store: IReadRunnerSettings,
        *,
        deferred_seed: IWriteRunnerSettings | None = None,
        identity_present: Callable[[], bool] | None = None,
        host_settings: HostSettingsReader | None = None,
    ) -> None:
        super().__init__(host, entry)
        #: The host's own record, whose entries every resolution lays over ``host``; ``None`` resolves against it.
        self.host_settings = host_settings
        self.store = store
        self._deferred_seed = deferred_seed
        self._identity_present = identity_present

    def seed_keys(self, entries: Mapping[str, Entry]) -> tuple[str, ...]:
        # An alias and its replacement represent one authored setting. Once either
        # has an entry, a later file edit must not seed the other spelling.
        blocked = {alias for alias, key in self.aliases.items() if key in entries}
        blocked.update(key for alias, key in self.aliases.items() if alias in entries)
        return tuple(key for key in (*self.keys, *self.aliases) if key not in blocked)

    def snapshot(self) -> tuple[SettingsSnapshot, RunnerConfig]:
        pair, config = self.snapshot_pair()
        return pair.runner, config

    def snapshot_pair(self) -> tuple[SettingsPair, RunnerConfig]:
        """The host revision and this runner's, as they stood together, and the configuration they resolve to."""
        pair = self.pair_record()
        return pair, self.resolve_pair(pair)

    def pair_record(self) -> SettingsPair:
        """Acquire both revisions coherently: the runner's is read between two reads of the host's, and kept only
        when the host's held across it, so the pair stood together at that read."""
        host_settings = self.host_settings
        if host_settings is None:
            return SettingsPair(None, self.snapshot_record())
        while True:
            host = host_settings.snapshot_record()
            runner = self.snapshot_record()
            if host_settings.revision() == host.revision:
                return SettingsPair(host, runner)

    def resolve_pair(self, pair: SettingsPair) -> RunnerConfig:
        return self.resolve_for_read(pair.runner.entries, host=self._host_at(pair.host))

    def _judging_host(self) -> HostConfig:
        return self._host_at(None)

    def _host_at(self, snapshot: SettingsSnapshot | None) -> HostConfig:
        if self.host_settings is None:
            return self.host
        return self.host_settings.resolve(snapshot if snapshot is not None else self.host_settings.snapshot_record())

    def snapshot_record(self) -> SettingsSnapshot:
        """Acquire the revision before resolving entries, including any deferred seed."""
        seed = self._deferred_seed
        if seed is not None and self._identity_present is not None:
            if not self._identity_present():
                return self.store.snapshot()
            assert self.entry.authored is not None
            seed.seed(
                self.entry.authored, (*self.keys, *self.aliases), validate=self.seed_validate, aliases=self.aliases
            )
            self._deferred_seed = None
        return self.store.snapshot()

    def snapshot_with_provenance(self) -> tuple[SettingsSnapshot, Mapping[str, SettingsChange]]:
        self.snapshot_record()
        return self.store.snapshot_with_provenance()

    def changes(self, *, limit: int, before: int | None = None) -> tuple[SettingsChange, ...]:
        return self.store.changes(limit=limit, before=before)

    def preview_seed(self) -> RunnerConfig:
        """Resolve the entries a first seed would produce, without writing them."""
        return self._preview_entries(self.store.snapshot().entries, host=self._host_at(None))

    def resolve_for_read(self, entries: Mapping[str, Entry], *, host: HostConfig | None = None) -> RunnerConfig:
        """Show authored inputs until registration makes a durable seed possible. ``host`` is the effective host
        configuration to resolve against, the host record's current one when absent."""
        effective = host if host is not None else self._host_at(None)
        if self._deferred_seed is not None:
            return self._preview_entries(entries, host=effective)
        return self.resolve(entries, host=effective)

    def _preview_entries(self, current: Mapping[str, Entry], *, host: HostConfig) -> RunnerConfig:
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
        return self.resolve(entries, host=host)

    def resolve(self, entries: Mapping[str, Entry], *, host: HostConfig | None = None) -> RunnerConfig:
        entry, errors = self._resolve(entries)
        if errors or entry.own is None:
            raise ValueError("; ".join(errors))
        effective = host if host is not None else self._host_at(None)
        # The token is the held token's, opened from the store — never a settings input.
        return effective.config_for_runner(entry, entry.own, "")


class HostApplyJudge:
    """A document apply's writes judged through the host's reader and each held runner's file entry, beside the
    state the apply leaves rather than the state the store holds now."""

    def __init__(self, host: HostSettingsReader, runners: Callable[[], Mapping[str, RunnerEntry]]) -> None:
        self._host = host
        self._runners = runners

    def host(self, entries: Mapping[str, Entry], records: HostRecordsSnapshot) -> Mapping[str, str]:
        return self._host.validate_with(entries, records if self._host.records is not None else None)

    def record(
        self, kind: RecordKind, candidate: HostRecord, entries: Mapping[str, Entry], records: HostRecordsSnapshot
    ) -> Mapping[str, str]:
        return self._host.record_problems(kind, candidate, entries=entries, records=records)

    def runner(
        self,
        runner_id: str,
        entries: Mapping[str, Entry],
        *,
        host_entries: Mapping[str, Entry],
        records: HostRecordsSnapshot,
    ) -> Mapping[str, str]:
        host = self._host.resolve_with(host_entries, records if self._host.records is not None else None)
        return RunnerEntriesJudge(host, self._runners()[runner_id]).validate(entries)


_judge_conforms: type[IJudgeHostApply] = HostApplyJudge
