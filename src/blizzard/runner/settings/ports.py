"""Runner-bound settings read and write seams."""

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
class SettingsChange:
    id: int
    recorded_at: datetime
    revision: int
    op: str
    door: str
    actor: str
    apply_id: str | None
    diff: tuple[FieldChange, ...]


class IReadRunnerSettings(Protocol):
    def snapshot(self) -> SettingsSnapshot: ...

    def changes(self, *, limit: int, before: int | None = None) -> tuple[SettingsChange, ...]: ...

    def provenance(self) -> Mapping[str, SettingsChange]: ...


class IWriteRunnerSettings(IReadRunnerSettings, Protocol):
    def seed(self, file: ConfigView, keys: Sequence[str], *, validate: Validate) -> None: ...

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
