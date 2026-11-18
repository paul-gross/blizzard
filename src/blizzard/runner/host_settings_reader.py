"""Resolve the host's stored entries over the host file held since boot, through the host file's own parse."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import replace
from typing import Any, cast

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.settings_record.vocabulary import Entry
from blizzard.foundation.store.utc import iso_utc
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.runner.config_keys import HOST_REGISTRY
from blizzard.runner.config_table import ConfigError
from blizzard.runner.host_config import HostConfig, HostConfigError
from blizzard.runner.process.child_env import ToolProfileHolder
from blizzard.runner.settings.keys import host_keys
from blizzard.runner.settings.ports import HostSettingsChange, IReadHostSettings, SettingsSnapshot

_log = get_logger("blizzard.runner.loop")


class HostSettingsReader:
    """The effective host configuration at a host revision: the file's boot keys, the host's entries laid over it.

    Resolved once per revision, so every pass at one revision shares one resolution."""

    def __init__(self, host: HostConfig, store: IReadHostSettings) -> None:
        self.host = host
        self.store = store
        self.keys = host_keys(HOST_REGISTRY)
        self._resolved: tuple[int, HostConfig] | None = None
        self._lock = threading.Lock()

    def snapshot_record(self) -> SettingsSnapshot:
        return self.store.snapshot()

    def revision(self) -> int:
        return self.store.revision()

    def snapshot(self) -> tuple[SettingsSnapshot, HostConfig]:
        snapshot = self.snapshot_record()
        return snapshot, self.resolve(snapshot)

    def snapshot_with_provenance(self) -> tuple[SettingsSnapshot, Mapping[str, HostSettingsChange]]:
        return self.store.snapshot_with_provenance()

    def current(self) -> HostConfig:
        """The effective host configuration at the store's current host revision."""
        return self.snapshot()[1]

    def live[T](self, pick: Callable[[HostConfig], T]) -> Callable[[], T]:
        """A read of ``pick`` at the store's current host revision, re-evaluated only when the revision moves; a
        revision that fails to resolve answers from the last resolution that succeeded."""
        held: list[tuple[int, T]] = []

        def read() -> T:
            revision = self.revision()
            if held and held[0][0] == revision:
                return held[0][1]
            snapshot = self.snapshot_record()
            try:
                config = self.resolve(snapshot)
            except ValueError:
                config = self.resolve_last()
            value = pick(config)
            held[:] = [(snapshot.revision, value)]
            return value

        return read

    def resolve(self, snapshot: SettingsSnapshot) -> HostConfig:
        with self._lock:
            if self._resolved is not None and self._resolved[0] == snapshot.revision:
                return self._resolved[1]
        resolved, errors = self._resolve(snapshot.entries)
        if errors:
            raise ValueError("; ".join(errors))
        with self._lock:
            self._resolved = (snapshot.revision, resolved)
        return resolved

    def resolve_last(self) -> HostConfig:
        """The last resolution that succeeded, else the host file's."""
        with self._lock:
            return self._resolved[1] if self._resolved is not None else self.host

    def validate(self, entries: Mapping[str, Entry]) -> Mapping[str, str]:
        unknown = {key: "not a writable host setting" for key in entries if key not in self.keys}
        if unknown:
            return unknown
        _, errors = self._resolve(entries)
        result: dict[str, str] = {}
        for error in errors:
            for key in self._error_keys(error, entries):
                result[key] = f"{result[key]}; {error}" if key in result else error
        return result

    def _error_keys(self, error: str, entries: Mapping[str, Entry]) -> set[str]:
        """Translate the host parse's diagnostics to record keys: a named key, else the keys of a named table, else
        every supplied entry, since a diagnostic is shown at boot and is not a structured API."""
        found = {key for key in self.keys if re.search(rf"(?<![\w.]){re.escape(key)}(?![\w])", error)}
        if not found:
            tables = set(re.findall(r"\[([a-z_]+)\]", error))
            found = {key for key in self.keys if key.partition(".")[0] in tables and key.partition(".")[2]}
            found = found & set(entries) or found
        return found or set(entries) or set(self.keys)

    def _resolve(self, entries: Mapping[str, Entry]) -> tuple[HostConfig, list[str]]:
        authored = self.host.authored
        if authored is None:
            # A host built in code has no file to lay entries over; it is its own resolution.
            return self.host, []
        body = deepcopy(dict(authored))
        for key, entry in entries.items():
            if key not in self.keys:
                continue
            *parents, leaf = key.split(".")
            table = body
            for part in parents:
                value = table.get(part)
                if not isinstance(value, dict):
                    value = {}
                    table[part] = value
                table = cast(dict[str, Any], value)
            if entry.at_default:
                table.pop(leaf, None)
            else:
                table[leaf] = deepcopy(entry.value)
        try:
            parsed = self.host.reparsed(body)
        except HostConfigError as exc:
            return self.host, list(exc.errors)
        except ConfigError as exc:
            return self.host, [str(exc)]
        return (
            replace(
                self.host,
                tick_seconds=parsed.tick_seconds,
                tracing=parsed.tracing,
                transcripts=parsed.transcripts,
                worker_stdout=parsed.worker_stdout,
                tools=parsed.tools,
                tool_path_prepend=parsed.tool_path_prepend,
                tool_env_passthrough=parsed.tool_env_passthrough,
            ),
            [],
        )


class HostSettingsInForce:
    """The host tick's own snapshot of the host record, taken at the start of each pass: the revision in force and
    since when, and the host's tool profile set from it. A revision that fails to resolve keeps the last good one in
    force and is reported once."""

    def __init__(
        self, reader: HostSettingsReader, *, tool_profile: ToolProfileHolder, tracing: TracingSettings, clock: IClock
    ) -> None:
        self.reader = reader
        self._tool_profile = tool_profile
        self._tracing = tracing
        self._clock = clock
        self._lock = threading.Lock()
        self.revision: int | None = None
        self.since: str | None = None
        self.failure: tuple[int, str] | None = None

    def take(self) -> HostConfig:
        """Snapshot the record and put it in force; the configuration the pass works from."""
        snapshot = self.reader.snapshot_record()
        try:
            effective = self.reader.resolve(snapshot)
        except ValueError as exc:
            with self._lock:
                if self.failure is None or self.failure[0] != snapshot.revision:
                    _log.error("host settings rebuild failed", revision=snapshot.revision, detail=str(exc))
                self.failure = (snapshot.revision, str(exc))
            return self.reader.resolve_last()
        self._tool_profile.hold(effective.host_view().tool_env(self._tracing))
        with self._lock:
            if self.revision != snapshot.revision or self.since is None:
                self.revision = snapshot.revision
                self.since = iso_utc(self._clock.now())
            self.failure = None
        return effective
