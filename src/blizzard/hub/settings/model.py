"""Hub-wide settings snapshots and their read/write seams."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.config_keys import ConfigView
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


class IReadHubSettingsAdmin(Protocol):
    def view(self) -> tuple[HubSettingsSnapshot, dict[str, tuple[int, datetime, str]]]: ...

    def changes(self, limit: int, before: int | None) -> tuple[list[dict[str, object]], int | None]: ...


class IWriteHubSettings(Protocol):
    def seed(self, keys: Sequence[str], file: ConfigView) -> None: ...

    def apply(
        self, change: Change, *, if_match: int | None, door: Door, actor: str, apply_id: str | None = None
    ) -> tuple[FieldChange, ...]: ...

    def new_apply_id(self) -> str: ...

    def apply_document(
        self, change: Change, *, actor: str, apply_id: str | None, dry_run: bool
    ) -> tuple[FieldChange, ...]: ...
