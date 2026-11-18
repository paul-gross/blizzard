"""Runner-bound and host settings read and write seams."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.config_keys import ConfigView
from blizzard.foundation.roles import domain_model
from blizzard.foundation.settings_record.vocabulary import Change, Door, Entry, FieldChange
from blizzard.foundation.settings_record.write import Validate


@domain_model
@dataclass(frozen=True)
class SettingsSnapshot:
    """One coherent revision and its entries."""

    revision: int
    entries: Mapping[str, Entry]


@domain_model
@dataclass(frozen=True)
class SettingsPair:
    """The host record and one runner's, as they stood together at one moment; ``host`` is ``None`` for a runner
    resolved without the host's record."""

    host: SettingsSnapshot | None
    runner: SettingsSnapshot

    def host_revision(self) -> int | None:
        return self.host.revision if self.host is not None else None


@domain_model
@dataclass(frozen=True)
class SettingsChange:
    id: int
    recorded_at: datetime
    revision: int
    op: str
    door: str
    actor: str
    apply_id: str | None
    diff: tuple[FieldChange, ...]


@domain_model
@dataclass(frozen=True)
class HostSettingsChange(SettingsChange):
    """A host change row: which of the host's records it moved, and which one within that kind."""

    record_kind: str
    record_key: str | None


class IReadRunnerSettings(Protocol):
    def snapshot(self) -> SettingsSnapshot: ...

    def snapshot_with_provenance(self) -> tuple[SettingsSnapshot, Mapping[str, SettingsChange]]: ...

    def changes(self, *, limit: int, before: int | None = None) -> tuple[SettingsChange, ...]: ...

    def provenance(self) -> Mapping[str, SettingsChange]: ...


class IWriteRunnerSettings(IReadRunnerSettings, Protocol):
    def seed(
        self, file: ConfigView, keys: Sequence[str], *, validate: Validate, aliases: Mapping[str, str] | None = None
    ) -> None: ...

    def write(
        self,
        change: Change,
        *,
        if_match: int | None,
        validate: Validate,
        door: Door,
        actor: str,
        apply_id: str | None = None,
    ) -> tuple[FieldChange, ...]: ...


class IReadHostSettings(Protocol):
    def snapshot(self) -> SettingsSnapshot: ...

    def revision(self) -> int: ...

    def snapshot_with_provenance(self) -> tuple[SettingsSnapshot, Mapping[str, HostSettingsChange]]: ...

    def changes(
        self,
        *,
        limit: int,
        before: int | None = None,
        record_kind: str | None = None,
        record_key: str | None = None,
    ) -> tuple[HostSettingsChange, ...]: ...


class IWriteHostSettings(IReadHostSettings, Protocol):
    def seed(self, source: ConfigView, keys: Sequence[str], *, validate: Validate) -> None: ...

    def write(
        self,
        change: Change,
        *,
        if_match: int | None,
        validate: Validate,
        door: Door,
        actor: str,
        apply_id: str | None = None,
    ) -> tuple[FieldChange, ...]: ...


class IWakeDrivers(Protocol):
    """The host's timed drivers, woken to re-read their intervals."""

    def wake(self) -> None: ...
