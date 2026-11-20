"""A host workspace or subscription record and every lifecycle rule a write of one follows.

A record is keyed by an immutable key (a workspace's ``id``, a subscription's ``slug``); its settings are every other
key of its host-file table. Each write that moves something bumps its revision and names the change; a write that
moves nothing is ``None`` and writes nothing."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from blizzard.foundation.roles import domain_model
from blizzard.foundation.settings_record.decision import decide
from blizzard.foundation.settings_record.refusals import SettingsInvalid
from blizzard.foundation.settings_record.vocabulary import Change, Door, Entry, FieldChange, Op


class RecordKind(StrEnum):
    """Which of the host's records a write names; the ``record_kind`` its change rows carry."""

    WORKSPACE = "workspace"
    SUBSCRIPTION = "subscription"

    @property
    def key_name(self) -> str:
        """The host-file table key the record is keyed by."""
        return _KEY_NAMES[self]

    @property
    def immutable(self) -> frozenset[str]:
        """The settings no edit may name, beside the key itself."""
        return _IMMUTABLE[self]


_KEY_NAMES: Mapping[RecordKind, str] = {RecordKind.WORKSPACE: "id", RecordKind.SUBSCRIPTION: "slug"}
_IMMUTABLE: Mapping[RecordKind, frozenset[str]] = {
    RecordKind.WORKSPACE: frozenset(),
    RecordKind.SUBSCRIPTION: frozenset({"provider"}),
}


class RecordFactOp(StrEnum):
    """What a lifecycle fact records."""

    RETIRED = "retired"
    ENABLED = "enabled"


class RecordUnknown(Exception):
    """No record of the kind holds the key."""

    def __init__(self, kind: RecordKind, key: str) -> None:
        super().__init__(f"no {kind} {key!r} on this host")
        self.kind = kind
        self.key = key


class RecordExists(Exception):
    """A create named a key a record of the kind already holds, retired or not."""

    def __init__(self, kind: RecordKind, key: str) -> None:
        super().__init__(f"{kind} {key!r} already exists on this host")
        self.kind = kind
        self.key = key


@domain_model
@dataclass(frozen=True)
class RecordActor:
    """Who writes, through which door, stamped by the writer's injected clock."""

    actor: str
    door: Door
    at: datetime


@domain_model
@dataclass(frozen=True)
class HostRecord:
    """One workspace or subscription as the store holds it: its key, revision, settings (every key of its table but
    the key), and whether it stands retired."""

    kind: RecordKind
    key: str
    revision: int
    settings: Mapping[str, object] = field(default_factory=dict[str, object])
    retired: bool = False

    def setting(self, name: str, default: object = None) -> object:
        """The setting ``name`` as the record states it; ``default`` when it states none."""
        return self.settings.get(name, default)

    def table(self) -> dict[str, object]:
        """The record as its host-file table: its key under the kind's key name, then its settings."""
        return {self.kind.key_name: self.key, **deepcopy(dict(self.settings))}

    @classmethod
    def created(cls, kind: RecordKind, key: str, settings: Mapping[str, object], *, op: Op = "create") -> RecordWrite:
        """A new record ``key`` from ``settings`` at revision 1: every key moved from absent. ``settings`` naming the
        key itself is refused, as is a blank key."""
        errors: dict[str, str] = {}
        if not key.strip():
            errors[kind.key_name] = "must be a non-blank string"
        if kind.key_name in settings:
            errors[kind.key_name] = "is the record's key; give it once, as the key"
        if errors:
            raise SettingsInvalid(errors)
        record = cls(kind=kind, key=key.strip(), revision=1, settings=deepcopy(dict(settings)))
        diff = tuple(FieldChange(name, None, Entry(record.settings[name])) for name in sorted(record.settings))
        return RecordWrite(record=record, prior=0, op=op, diff=diff)

    def edit(
        self, sets: Mapping[str, object], unsets: frozenset[str] = frozenset(), *, if_match: int | None
    ) -> RecordWrite | None:
        """A sparse merge: each of ``sets`` takes its whole value, each of ``unsets`` returns to its default, every
        other key stands. Refused on a missing or stale revision, and on naming the key or an immutable setting.
        Legal on a retired record, which stays retired. ``None`` when nothing moves."""
        both = sorted(unsets & sets.keys())
        if both:
            raise SettingsInvalid(dict.fromkeys(both, "both set and unset in one edit"))
        decision = decide(
            self.revision,
            {name: Entry(value) for name, value in self.settings.items()},
            Change("edit", deepcopy(dict(sets)), unsets),
            if_match=if_match,
        )
        fixed = {self.kind.key_name, *self.kind.immutable}
        named = sorted(fixed & (sets.keys() | unsets))
        if named:
            raise SettingsInvalid(dict.fromkeys(named, "is immutable; it cannot be edited"))
        # A record states no default: an unset leaves the setting absent, and unsetting an absent one moves nothing.
        diff = tuple(
            sorted(
                (change for change in decision.diff if change.old is not None or not change.new.at_default),
                key=lambda change: change.key,
            )
        )
        if not diff:
            return None
        candidate = {name: deepcopy(entry.value) for name, entry in decision.candidate.items() if not entry.at_default}
        return RecordWrite(
            record=HostRecord(self.kind, self.key, self.revision + 1, candidate, self.retired),
            prior=self.revision,
            op="edit",
            diff=diff,
        )

    def retire(self) -> RecordWrite | None:
        """Retire the record; of a retired one, ``None``: nothing is written."""
        if self.retired:
            return None
        return self._lifecycle("retire", RecordFactOp.RETIRED, retired=True)

    def enable(self) -> RecordWrite | None:
        """Reverse the retirement; of an enabled one, ``None``: nothing is written."""
        if not self.retired:
            return None
        return self._lifecycle("enable", RecordFactOp.ENABLED, retired=False)

    def _lifecycle(self, op: Op, fact: RecordFactOp, *, retired: bool) -> RecordWrite:
        record = HostRecord(self.kind, self.key, self.revision + 1, self.settings, retired)
        return RecordWrite(record=record, prior=self.revision, op=op, diff=(), fact=fact)


@domain_model
@dataclass(frozen=True)
class RecordWrite:
    """One write a record's rule decided: the record as it will stand, the revision it was decided against (0 for a
    create), the change row's op and diff, and the lifecycle fact it appends, if any."""

    record: HostRecord
    prior: int
    op: Op
    diff: tuple[FieldChange, ...]
    fact: RecordFactOp | None = None

    def creates(self) -> bool:
        return self.prior == 0

    def settings_moved(self) -> bool:
        """Whether the write moves a setting, so the candidate is judged as the host file would be."""
        return self.creates() or bool(self.diff)


@domain_model
@dataclass(frozen=True)
class HostRecordsSnapshot:
    """The host's workspaces and subscriptions as one pass read them, each in key order: the active ones, and the
    retired workspaces beside them, which still serve what they hold."""

    workspaces: tuple[HostRecord, ...] = ()
    subscriptions: tuple[HostRecord, ...] = ()
    retired_workspaces: tuple[HostRecord, ...] = ()

    def versions(self) -> tuple[tuple[str, int], ...]:
        """Each record's kind-qualified key and revision, retired workspaces included: moves whenever any record is
        written, created, retired or enabled."""
        return tuple(
            (f"{record.kind}:{record.key}", record.revision)
            for record in (*self.workspaces, *self.subscriptions, *self.retired_workspaces)
        )
