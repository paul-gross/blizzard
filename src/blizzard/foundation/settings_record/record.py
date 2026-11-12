"""The seam each daemon's settings tables implement, so the write rules live once."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from sqlalchemy import Connection

from blizzard.foundation.settings_record.vocabulary import Door, Entry, FieldChange, Op


class ISettingsRecord(Protocol):
    """A daemon's settings tables. Every method runs on the caller's transaction."""

    def revision(self, conn: Connection) -> int: ...

    def entries(self, conn: Connection) -> Mapping[str, Entry]: ...

    def has_entry(self, conn: Connection, key: str) -> bool: ...

    def bump_revision(self, conn: Connection) -> int:
        """Advance the revision and return the new one."""
        ...

    def write_entries(self, conn: Connection, moved: Mapping[str, Entry], *, revision: int) -> None: ...

    def append_change(
        self,
        conn: Connection,
        *,
        diff: Sequence[FieldChange],
        op: Op,
        door: Door,
        actor: str,
        apply_id: str | None,
        revision: int,
    ) -> None: ...
