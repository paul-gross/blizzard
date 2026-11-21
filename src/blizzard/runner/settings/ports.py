"""Runner-bound and host settings read and write seams."""

from __future__ import annotations

from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from blizzard.foundation.config_keys import ConfigView
from blizzard.foundation.roles import domain_model
from blizzard.foundation.settings_record.validator import Validate
from blizzard.foundation.settings_record.vocabulary import Change, Door, Entry, FieldChange
from blizzard.runner.host_records.model import HostRecord, HostRecordsSnapshot, RecordActor, RecordKind, RecordWrite

if TYPE_CHECKING:
    from blizzard.runner.settings.apply import HeldHostState, HostSettingsPlan


@domain_model
@dataclass(frozen=True)
class SettingsSnapshot:
    """One coherent revision and its entries."""

    revision: int
    entries: Mapping[str, Entry]

    def version(self) -> Hashable:
        """What moves whenever anything this snapshot resolves from moves: its revision."""
        return self.revision


@domain_model
@dataclass(frozen=True)
class SettingsPair:
    """The host record and one runner's, as they stood together at one moment; ``host`` is ``None`` for a runner
    resolved without the host's record."""

    host: SettingsSnapshot | None
    runner: SettingsSnapshot

    def host_revision(self) -> int | None:
        return self.host.revision if self.host is not None else None

    def host_version(self) -> Hashable:
        """What moves whenever anything the host's side of the pair resolves from moves."""
        return self.host.version() if self.host is not None else None


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


@domain_model
@dataclass(frozen=True)
class PreparedApply:
    """A planned document apply, its workspace and subscription writes already judged, and how each settings write
    it makes judges its candidate: the host record's, and each stated runner's by id."""

    plan: HostSettingsPlan
    validate_host: Validate
    validate_runner: Mapping[str, Validate]


@domain_model
@dataclass(frozen=True)
class ApplyBy:
    """Who an apply writes as: every change row it writes carries door ``apply``, ``actor`` and ``apply_id``."""

    actor: str
    apply_id: str | None
    at: datetime


class IApplyHostSettings(Protocol):
    """The host settings document's unit of work: one transaction over the host record, the workspace and
    subscription records and the stated runners' records."""

    def apply(
        self,
        runners: Sequence[str],
        prepare: Callable[[HeldHostState], PreparedApply],
        *,
        by: ApplyBy,
        dry_run: bool,
    ) -> HostSettingsPlan:
        """Take every lock — the host record, then its workspace and subscription records, then each of ``runners``
        the host holds, in id order — read the held state under them, ``prepare`` the apply against it, then make
        its writes. Rolled back whole on any refusal, and on ``dry_run`` after every write ran; the plan made."""
        ...

    def held(self) -> HeldHostState:
        """The held state as one read sees it, every hosted runner's record included — what an export states."""
        ...


class IJudgeHostApply(Protocol):
    """How a document apply's writes are judged: each beside the host entries and records the whole apply leaves, as
    the host file would be at boot."""

    def host(self, entries: Mapping[str, Entry], records: HostRecordsSnapshot) -> Mapping[str, str]: ...

    def record(
        self, kind: RecordKind, candidate: HostRecord, entries: Mapping[str, Entry], records: HostRecordsSnapshot
    ) -> Mapping[str, str]: ...

    def runner(
        self,
        runner_id: str,
        entries: Mapping[str, Entry],
        *,
        host_entries: Mapping[str, Entry],
        records: HostRecordsSnapshot,
    ) -> Mapping[str, str]: ...


class IApplyHandle(Protocol):
    """The one write transaction a host settings document's apply hands each of its members; opaque here."""


class IApplySettingsRecord(Protocol):
    """A settings record — the host's, or one runner's — as a document apply's member, on the apply's transaction."""

    def lock_locked(self, handle: IApplyHandle) -> None: ...

    def snapshot_locked(self, handle: IApplyHandle) -> SettingsSnapshot: ...

    def write_locked(
        self,
        handle: IApplyHandle,
        change: Change,
        *,
        if_match: int | None,
        validate: Validate,
        door: Door,
        actor: str,
        apply_id: str | None,
    ) -> tuple[FieldChange, ...]: ...


class IApplyHostRecords(Protocol):
    """One kind of the host's workspace and subscription records as a document apply's member."""

    def lock_locked(self, handle: IApplyHandle) -> None: ...

    def records_locked(self, handle: IApplyHandle) -> tuple[HostRecord, ...]:
        """Every record of the kind, retired ones included, in key order."""
        ...

    def apply_locked(self, handle: IApplyHandle, write: RecordWrite, by: RecordActor) -> None: ...
