"""Hub-wide settings snapshots and their read/write seams."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.foundation.settings_record.vocabulary import Change, Door, Entry, FieldChange


@domain_model
@dataclass(frozen=True)
class HubSettingsSnapshot:
    revision: int
    entries: Mapping[str, Entry]


@domain_model
@dataclass(frozen=True)
class HubSettingsApply:
    """A revision-conditioned settings change and its audit context."""

    change: Change
    if_match: int | None
    door: Door
    actor: str
    apply_id: str | None = None


class IHubSettingsSnapshots(Protocol):
    def snapshot(self) -> HubSettingsSnapshot: ...


class IReadHubSettings(IHubSettingsSnapshots, Protocol):
    def revision(self) -> int: ...


class IWriteHubSettings(Protocol):
    def apply(
        self, change: Change, *, if_match: int | None, door: Door, actor: str, apply_id: str | None = None
    ) -> tuple[FieldChange, ...]: ...
