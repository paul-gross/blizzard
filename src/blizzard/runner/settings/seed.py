"""The whole seed of one runner's settings record, as a write-side step apart from every read."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from blizzard.foundation.config_keys import ConfigView
from blizzard.foundation.settings_record.validator import Validate
from blizzard.runner.settings.ports import IWriteRunnerSettings


class RunnerSettingsSeed:
    """Seeds one runner's record from its boot-held file entry."""

    def __init__(
        self,
        store: IWriteRunnerSettings,
        authored: ConfigView,
        keys: Sequence[str],
        aliases: Mapping[str, str],
        *,
        validate: Validate,
    ) -> None:
        self._store = store
        self._authored = authored
        self._keys = tuple(keys)
        self._aliases = aliases
        self._validate = validate

    def run(self) -> None:
        self._store.seed(self._authored, (*self._keys, *self._aliases), validate=self._validate, aliases=self._aliases)
