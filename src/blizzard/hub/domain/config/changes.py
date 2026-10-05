"""The change-log vocabulary: who changed which record, through which door, and how.

A :class:`ConfigChange` is decided by a record's model and handed by :class:`ConfigAuthoring` to the write
repository, which commits it in the same transaction as the record write
(``bzh:configured-record``). No value of a secret ever appears in a diff."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol

from blizzard.foundation.roles import domain_model


class Door(StrEnum):
    """The surface a write arrived through. ``apply`` and ``migration`` are set
    server-side and never read from a request."""

    BOARD = "board"
    CLI = "cli"
    API = "api"
    APPLY = "apply"
    MIGRATION = "migration"

    @classmethod
    def claimed(cls, raw: str | None) -> Door:
        """The door a client claims for itself. Only ``cli`` and ``board`` may be claimed;
        ``apply``, ``migration``, an unknown value, or none at all read as ``api``."""
        if raw in (cls.CLI.value, cls.BOARD.value):
            return cls(raw)
        return cls.API


class RecordKind(StrEnum):
    WORK_SOURCE = "work_source"
    SECRET = "secret"
    REPOSITORY = "repository"


class ChangeOp(StrEnum):
    CREATE = "create"
    EDIT = "edit"
    RETIRE = "retire"
    ENABLE = "enable"
    REPLACE = "replace"


#: The wire name a retire or enable change reports its flip under.
RETIRED_FIELD = "retired"


class RecordState(StrEnum):
    """A configured record's lifecycle state: retirement is a reversible brake."""

    ACTIVE = "active"
    RETIRED = "retired"

    @classmethod
    def of(cls, retired: bool) -> RecordState:
        return cls.RETIRED if retired else cls.ACTIVE


class Verdict(StrEnum):
    """What a verb does from a state: moves it, writes nothing, or is refused."""

    LEGAL = "legal"
    NO_OP = "no_op"
    REFUSED = "refused"


#: Which lifecycle verbs are legal from which state for a work source or repository; editing a retired one is legal.
FIELDED_RECORD_TRANSITIONS: Mapping[RecordState, Mapping[ChangeOp, Verdict]] = MappingProxyType(
    {
        RecordState.ACTIVE: MappingProxyType(
            {ChangeOp.EDIT: Verdict.LEGAL, ChangeOp.RETIRE: Verdict.LEGAL, ChangeOp.ENABLE: Verdict.NO_OP}
        ),
        RecordState.RETIRED: MappingProxyType(
            {ChangeOp.EDIT: Verdict.LEGAL, ChangeOp.RETIRE: Verdict.NO_OP, ChangeOp.ENABLE: Verdict.LEGAL}
        ),
    }
)

#: Which lifecycle verbs are legal from which state for a secret; a retired secret's value cannot be replaced.
SECRET_TRANSITIONS: Mapping[RecordState, Mapping[ChangeOp, Verdict]] = MappingProxyType(
    {
        RecordState.ACTIVE: MappingProxyType(
            {ChangeOp.REPLACE: Verdict.LEGAL, ChangeOp.RETIRE: Verdict.LEGAL, ChangeOp.ENABLE: Verdict.NO_OP}
        ),
        RecordState.RETIRED: MappingProxyType(
            {ChangeOp.REPLACE: Verdict.REFUSED, ChangeOp.RETIRE: Verdict.NO_OP, ChangeOp.ENABLE: Verdict.LEGAL}
        ),
    }
)


@domain_model
@dataclass(frozen=True)
class ChangeContext:
    """Who is writing and through which door — built at the edge, never from a body field."""

    actor: str
    door: Door
    #: Groups the rows one apply writes; ``None`` for every other door and for a dry run.
    apply_id: str | None = None


@domain_model
@dataclass(frozen=True)
class FieldChange:
    """One field's change, named by its wire name."""

    field: str
    old: object
    new: object


@domain_model
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

    @classmethod
    def of(
        cls,
        ctx: ChangeContext,
        kind: RecordKind,
        key: str,
        revision: int,
        op: ChangeOp,
        changes: tuple[FieldChange, ...],
        at: datetime,
    ) -> ConfigChange:
        """The change row ``ctx`` writes to ``kind``/``key`` at ``revision``."""
        return cls(
            recorded_at=at,
            actor=ctx.actor,
            door=ctx.door,
            record_kind=kind,
            record_key=key,
            revision=revision,
            op=op,
            diff=changes,
            apply_id=ctx.apply_id,
        )


@domain_model
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
