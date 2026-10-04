"""The change-log vocabulary: who changed which record, through which door, and how.

A :class:`ConfigChange` is built by :class:`ConfigAuthoring` and handed to the write
repository, which commits it in the same transaction as the record write
(``bzh:configured-record``). No value of a secret ever appears in a diff."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol


class Door(StrEnum):
    """The surface a write arrived through. ``apply`` and ``migration`` are set
    server-side and never read from a request."""

    BOARD = "board"
    CLI = "cli"
    API = "api"
    APPLY = "apply"
    MIGRATION = "migration"


class RecordKind(StrEnum):
    WORK_SOURCE = "work_source"
    SECRET = "secret"


class ChangeOp(StrEnum):
    CREATE = "create"
    EDIT = "edit"
    RETIRE = "retire"
    ENABLE = "enable"
    REPLACE = "replace"


@dataclass(frozen=True)
class ChangeContext:
    """Who is writing and through which door — built at the edge, never from a body field."""

    actor: str
    door: Door


@dataclass(frozen=True)
class FieldChange:
    """One field's change, named by its wire name."""

    field: str
    old: object
    new: object


@dataclass(frozen=True)
class ConfigChange:
    """One row of the change log. ``id`` is ``None`` until the store assigns it."""

    recorded_at: datetime
    actor: str
    door: Door
    record_kind: RecordKind
    record_key: str
    revision: int
    op: ChangeOp
    diff: tuple[FieldChange, ...] = ()
    apply_id: str | None = None
    id: int | None = None


@dataclass(frozen=True)
class RecordRef:
    """A configured record named by kind and key."""

    kind: RecordKind
    key: str

    def __str__(self) -> str:
        return f"{self.kind.value} {self.key}"


class IReadConfigChanges(Protocol):
    def page(
        self, *, before: int | None, limit: int, record_kind: RecordKind | None, record_key: str | None
    ) -> list[ConfigChange]:
        """Newest-first by ``id``, strictly below ``before`` when given, at most ``limit`` rows."""
        ...


class ISecretReferences(Protocol):
    """The active configured records that name a secret."""

    def referrers_of(self, names: list[str]) -> dict[str, list[RecordRef]]:
        """For each name, its active referencing records, ordered by kind then key
        (``bzh:bulk-reconstitution``); a name nothing references maps to ``[]``."""
        ...
