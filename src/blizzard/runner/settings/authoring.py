"""Runner and host settings edits, independent of the calling transport."""

from collections.abc import Mapping
from typing import Protocol

from blizzard.foundation.config_keys import ConfigRegistry
from blizzard.foundation.settings_record.refusals import SettingsInvalid
from blizzard.foundation.settings_record.vocabulary import Change, Door, Entry, FieldChange
from blizzard.runner.settings.policy import check_host_keys, check_keys
from blizzard.runner.settings.ports import IWakeDrivers, IWriteHostSettings, IWriteRunnerSettings, SettingsSnapshot


class SettingsAuthoringReader(Protocol):
    def snapshot_record(self) -> SettingsSnapshot: ...

    def validate(self, entries: Mapping[str, Entry]) -> Mapping[str, str]: ...


class RunnerSettingsAuthoring:
    def __init__(self, reader: SettingsAuthoringReader, store: IWriteRunnerSettings, registry: ConfigRegistry) -> None:
        self._reader = reader
        self._store = store
        self._registry = registry

    def edit(
        self, sets: Mapping[str, object], unsets: frozenset[str], *, if_match: int | None, door: Door, actor: str
    ) -> None:
        both = sets.keys() & unsets
        if both:
            raise SettingsInvalid(dict.fromkeys(sorted(both), "cannot both set and unset"))
        check_keys(self._registry, [*sets, *unsets])
        self._reader.snapshot_record()
        self._store.write(
            Change("edit", sets=sets, unsets=unsets),
            if_match=if_match,
            validate=self._reader.validate,
            door=door,
            actor=actor,
        )


class HostSettingsValidator(Protocol):
    def validate(self, entries: Mapping[str, Entry]) -> Mapping[str, str]: ...


#: The host keys a timed driver's wait is counted in; a write moving one wakes every driver to re-read it.
INTERVAL_KEYS = frozenset({"tick_seconds", "tracing.sweep_seconds"})


class HostSettingsAuthoring:
    """The host record's one write path: every door's edit is refused or committed here. A committed write that
    moves an interval key wakes ``drivers``, after its commit."""

    def __init__(
        self,
        reader: HostSettingsValidator,
        store: IWriteHostSettings,
        registry: ConfigRegistry,
        *,
        drivers: IWakeDrivers | None = None,
    ) -> None:
        self._reader = reader
        self._store = store
        self._registry = registry
        self._drivers = drivers

    def edit(
        self, sets: Mapping[str, object], unsets: frozenset[str], *, if_match: int | None, door: Door, actor: str
    ) -> tuple[FieldChange, ...]:
        both = sets.keys() & unsets
        if both:
            raise SettingsInvalid(dict.fromkeys(sorted(both), "cannot both set and unset"))
        check_host_keys(self._registry, [*sets, *unsets])
        moved = self._store.write(
            Change("edit", sets=sets, unsets=unsets),
            if_match=if_match,
            validate=self._reader.validate,
            door=door,
            actor=actor,
        )
        if self._drivers is not None and any(change.key in INTERVAL_KEYS for change in moved):
            self._drivers.wake()
        return moved
