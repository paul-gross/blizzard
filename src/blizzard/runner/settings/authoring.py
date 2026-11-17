"""Runner settings edits, independent of the calling transport."""

from collections.abc import Mapping
from typing import Protocol

from blizzard.foundation.config_keys import ConfigRegistry
from blizzard.foundation.settings_record.refusals import SettingsInvalid
from blizzard.foundation.settings_record.vocabulary import Change, Door, Entry
from blizzard.runner.settings.policy import check_keys
from blizzard.runner.settings.ports import IWriteRunnerSettings, SettingsSnapshot


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
