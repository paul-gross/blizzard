"""Resolve the host's stored entries over the host file held since boot, through the host file's own parse."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Hashable, Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, cast

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.foundation.settings_record.vocabulary import Entry
from blizzard.foundation.store.utc import iso_utc
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.runner.config import HostWorkspace
from blizzard.runner.config_keys import HOST_REGISTRY
from blizzard.runner.config_table import ConfigError
from blizzard.runner.host_config import HostConfig, HostConfigError
from blizzard.runner.host_records.model import HostRecord, HostRecordsSnapshot, RecordKind
from blizzard.runner.host_records.ports import IReadHostRecordRepository
from blizzard.runner.process.child_env import ToolProfileHolder
from blizzard.runner.settings.keys import host_keys
from blizzard.runner.settings.ports import HostSettingsChange, IReadHostSettings, SettingsSnapshot

_log = get_logger("blizzard.runner.loop")


@domain_model
@dataclass(frozen=True)
class HostSettingsSnapshot(SettingsSnapshot):
    """The host record and its active workspace and subscription records, as one pass read them; ``records`` is
    ``None`` for a host that keeps no records, which resolves its workspaces and subscriptions from its file."""

    records: HostRecordsSnapshot | None = None

    def version(self) -> Hashable:
        """Moves when the host record moves or any workspace or subscription record does."""
        return (self.revision, self.records.versions() if self.records is not None else None)


@dataclass(frozen=True)
class HostRecordReads:
    """The host's workspace and subscription records, read for every pass's snapshot."""

    workspaces: IReadHostRecordRepository
    subscriptions: IReadHostRecordRepository

    def snapshot(self) -> HostRecordsSnapshot:
        """The active records of both kinds and the retired workspaces, one read of each kind."""
        workspaces = self.workspaces.records(retired=True)
        return HostRecordsSnapshot(
            workspaces=tuple(record for record in workspaces if not record.retired),
            subscriptions=self.subscriptions.records(),
            retired_workspaces=tuple(record for record in workspaces if record.retired),
        )


class HostSettingsReader:
    """The effective host configuration at a host revision: the file's boot keys, the host's entries laid over it,
    and — for a host keeping records — its workspaces and subscriptions taken from its active records.

    Resolved once per version, so every pass at one version shares one resolution."""

    def __init__(self, host: HostConfig, store: IReadHostSettings, records: HostRecordReads | None = None) -> None:
        self.host = host
        self.store = store
        self.records = records
        self.keys = host_keys(HOST_REGISTRY)
        self._resolved: tuple[Hashable, HostConfig] | None = None
        self._lock = threading.Lock()

    def snapshot_record(self) -> HostSettingsSnapshot:
        held = self.store.snapshot()
        records = self.records.snapshot() if self.records is not None else None
        return HostSettingsSnapshot(held.revision, held.entries, records)

    def _records_of(self, snapshot: SettingsSnapshot) -> HostRecordsSnapshot | None:
        """``snapshot``'s records; read now for a snapshot of the host record alone."""
        if isinstance(snapshot, HostSettingsSnapshot):
            return snapshot.records
        return self.records.snapshot() if self.records is not None else None

    def _host_snapshot(self, snapshot: SettingsSnapshot) -> HostSettingsSnapshot:
        if isinstance(snapshot, HostSettingsSnapshot):
            return snapshot
        return HostSettingsSnapshot(snapshot.revision, snapshot.entries, self._records_of(snapshot))

    def revision(self) -> int:
        return self.store.revision()

    def snapshot(self) -> tuple[SettingsSnapshot, HostConfig]:
        snapshot = self.snapshot_record()
        return snapshot, self.resolve(snapshot)

    def snapshot_with_provenance(self) -> tuple[SettingsSnapshot, Mapping[str, HostSettingsChange]]:
        return self.store.snapshot_with_provenance()

    def changes(
        self, *, limit: int, before: int | None = None, record_kind: str | None = None, record_key: str | None = None
    ) -> tuple[HostSettingsChange, ...]:
        return self.store.changes(limit=limit, before=before, record_kind=record_kind, record_key=record_key)

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
        held = self._host_snapshot(snapshot)
        version = held.version()
        with self._lock:
            if self._resolved is not None and self._resolved[0] == version:
                return self._resolved[1]
        resolved, errors = self._resolve(held.entries, held.records)
        if errors:
            raise ValueError("; ".join(errors))
        with self._lock:
            self._resolved = (version, resolved)
        return resolved

    def resolve_last(self) -> HostConfig:
        """The last resolution that succeeded, else the host file's."""
        with self._lock:
            return self._resolved[1] if self._resolved is not None else self.host

    def validate(self, entries: Mapping[str, Entry]) -> Mapping[str, str]:
        unknown = {key: "not a writable host setting" for key in entries if key not in self.keys}
        if unknown:
            return unknown
        _, errors = self._resolve(entries, self.records.snapshot() if self.records is not None else None)
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

    def record_problems(self, kind: RecordKind, candidate: HostRecord) -> Mapping[str, str]:
        """Each failing setting of ``candidate`` and why, judged by the host file's parse — an active candidate beside
        the host's current entries and its other active records, a retired one on its own as it is served — and, for
        a workspace, its prompt resolved as its provider's build resolves it. So a write refuses exactly what the
        file would fail at boot and what the next rebuild would fail at."""
        current = self.records.snapshot() if self.records is not None else HostRecordsSnapshot()
        mine = current.workspaces if kind is RecordKind.WORKSPACE else current.subscriptions
        if candidate.retired:
            placed: tuple[HostRecord, ...] = (candidate,)
        else:
            placed = (*(record for record in mine if record.key != candidate.key), candidate)
        records = (
            replace(current, workspaces=placed)
            if kind is RecordKind.WORKSPACE
            else replace(current, subscriptions=placed)
        )
        parsed, errors = self._resolve(self.store.snapshot().entries, records)
        if not errors and kind is RecordKind.WORKSPACE:
            for workspace in parsed.workspaces:
                if workspace.id == candidate.key:
                    try:
                        workspace.resolved_prompt(self.host.root)
                    except ConfigError as exc:
                        errors.append(str(exc))
        names = (kind.key_name, *candidate.settings)
        result: dict[str, str] = {}
        for error in errors:
            found = [name for name in names if re.search(rf"(?<![\w.]){re.escape(name)}(?![\w])", error)]
            for name in found or [kind.key_name]:
                result[name] = f"{result[name]}; {error}" if name in result else error
        return result

    def retired_workspaces(self, snapshot: HostSettingsSnapshot) -> tuple[HostWorkspace, ...]:
        """The snapshot's retired workspaces, each parsed from its record on its own beside the snapshot's active
        subscriptions; one whose table no longer parses is left out and logged, the others served."""
        if snapshot.records is None:
            return ()
        parsed: list[HostWorkspace] = []
        for record in snapshot.records.retired_workspaces:
            config, errors = self._resolve(snapshot.entries, replace(snapshot.records, workspaces=(record,)))
            if errors:
                _log.error(
                    "retired workspace unreadable — what it holds waits", workspace=record.key, detail="; ".join(errors)
                )
                continue
            parsed.extend(config.workspaces)
        return tuple(parsed)

    def _resolve(
        self, entries: Mapping[str, Entry], records: HostRecordsSnapshot | None
    ) -> tuple[HostConfig, list[str]]:
        authored = self.host.authored
        if authored is None:
            # A host built in code has no file to lay entries over; it is its own resolution.
            return self.host, []
        body = deepcopy(dict(authored))
        if records is not None:
            # The host's records, not its file, say which workspaces and subscriptions it has.
            body["workspace"] = [record.table() for record in records.workspaces]
            body["subscription"] = [record.table() for record in records.subscriptions]
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
                workspaces=parsed.workspaces if records is not None else self.host.workspaces,
                subscriptions=parsed.subscriptions if records is not None else self.host.subscriptions,
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
        #: The revision that failed to resolve, its error, and since when; ``None`` while the latest resolves.
        self.failure: tuple[int, str, str] | None = None
        #: The snapshot last put in force; ``None`` before the first.
        self.taken: HostSettingsSnapshot | None = None

    def status[T](self, read: Callable[[], T]) -> tuple[T, int | None, str | None, tuple[int, str, str] | None]:
        """``read`` of the store beside the host tick's published state, the three read as one."""
        with self._lock:
            return read(), self.revision, self.since, self.failure

    def take(self) -> HostConfig:
        """Snapshot the record and put it in force; the configuration the pass works from."""
        snapshot = self.reader.snapshot_record()
        try:
            effective = self.reader.resolve(snapshot)
        except ValueError as exc:
            with self._lock:
                if self.failure is None or self.failure[0] != snapshot.revision:
                    _log.error("host settings rebuild failed", revision=snapshot.revision, detail=str(exc))
                    self.failure = (snapshot.revision, str(exc), iso_utc(self._clock.now()))
            return self.reader.resolve_last()
        self._tool_profile.hold(effective.host_view().tool_env(self._tracing))
        with self._lock:
            self.taken = snapshot
            if self.revision != snapshot.revision or self.since is None:
                self.revision = snapshot.revision
                self.since = iso_utc(self._clock.now())
            self.failure = None
        return effective


@dataclass(frozen=True)
class HostRecordValidation:
    """A workspace or subscription record judged by the host file's own parse, through the host's reader."""

    reader: HostSettingsReader

    def problems(self, kind: RecordKind, candidate: HostRecord) -> Mapping[str, str]:
        return self.reader.record_problems(kind, candidate)
