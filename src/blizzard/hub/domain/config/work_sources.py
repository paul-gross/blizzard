"""Work-source records: the stored shape, validation, sparse merge, and the seams."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import ClassVar, Protocol

from blizzard.foundation.roles import domain_model
from blizzard.hub.config import KNOWN_WORK_SOURCE_PROVIDERS, RESERVED_HUB_SOURCE_NAME
from blizzard.hub.domain.config.changes import (
    FIELDED_RECORD_TRANSITIONS,
    RETIRED_FIELD,
    ChangeContext,
    ChangeOp,
    ConfigChange,
    FieldChange,
    RecordKind,
    RecordState,
    Verdict,
)
from blizzard.hub.domain.kernel.unset import UNSET, UnsetType

#: Providers whose items cannot be read without a credential.
PROVIDERS_NEEDING_CREDENTIAL = frozenset({"github"})

_GITHUB_LOCATOR = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

#: The mutable fields, in the order a diff lists them.
_FIELDS = ("provider", "locator", "api_base", "web_base", "annotate", "secret")
_NULLABLE = frozenset({"api_base", "web_base", "secret"})


class ConfigFieldError(ValueError):
    """A write is invalid on one field — names it, so the edge can answer 422 on it."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


class ConfigRevisionConflict(Exception):
    """A write read a revision the store no longer holds — names the current one."""

    def __init__(self, kind: str, key: str, *, current: int) -> None:
        super().__init__(f"{kind} {key} is at revision {current}")
        self.current = current


class WorkSourceNameTaken(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(f"work source {name} already exists")
        self.name = name


class WorkSourceLocatorTaken(Exception):
    """``(provider, locator)`` already belongs to ``holder`` — retired holders keep their claim."""

    def __init__(self, provider: str, locator: str, *, holder: str) -> None:
        super().__init__(f"{provider} locator {locator} already belongs to work source {holder}; enable it instead")
        self.holder = holder


class WorkSourceSecretUnavailable(ConfigFieldError):
    """The referenced secret is missing or retired."""

    def __init__(self, secret: str, *, retired: bool) -> None:
        super().__init__("secret", f"secret {secret} is {'retired' if retired else 'unknown'}")


class BuiltInWorkSource(Exception):
    """The built-in ``hub`` source is never written."""

    def __init__(self) -> None:
        super().__init__(f"work source {RESERVED_HUB_SOURCE_NAME} is built in and cannot be changed")


@domain_model
@dataclass(frozen=True)
class WorkSourceFields:
    """The mutable fields of a work source, by wire name."""

    provider: str
    locator: str
    api_base: str | None
    web_base: str | None
    annotate: bool
    secret: str | None


@domain_model
@dataclass(frozen=True)
class ConfiguredWorkSource:
    """A stored work source. ``retired`` derives from the newest lifecycle fact.

    Each verb returns the record to write with the :class:`ConfigChange` committed beside it,
    or ``None`` when the verb changes nothing and so writes nothing."""

    TRANSITIONS: ClassVar[Mapping[RecordState, Mapping[ChangeOp, Verdict]]] = FIELDED_RECORD_TRANSITIONS

    name: str
    fields: WorkSourceFields
    revision: int
    created_at: datetime
    created_by: str
    retired: bool = False

    @classmethod
    def new(
        cls, name: str, fields: WorkSourceFields, ctx: ChangeContext, *, at: datetime
    ) -> tuple[ConfiguredWorkSource, ConfigChange]:
        validate_name(name)
        validate_fields(fields)
        record = cls(name=name, fields=fields, revision=1, created_at=at, created_by=ctx.actor)
        return record, ConfigChange.of(ctx, RecordKind.WORK_SOURCE, name, 1, ChangeOp.CREATE, diff(None, fields), at)

    def require_revision(self, if_match: int | None) -> None:
        """:class:`ConfigRevisionConflict` when ``if_match`` names a revision other than the stored one."""
        if if_match is not None and if_match != self.revision:
            raise ConfigRevisionConflict("work source", self.name, current=self.revision)

    def edit(
        self, edit: WorkSourceEdit, ctx: ChangeContext, *, if_match: int | None, at: datetime
    ) -> tuple[ConfiguredWorkSource, ConfigChange] | None:
        """Apply a sparse edit, legal from either state. The revision check comes first, even
        for an edit that changes nothing; an empty diff never re-validates the stored fields."""
        self.require_revision(if_match)
        merged = merge(self.fields, edit)
        changes = diff(self.fields, merged)
        if not changes:
            return None
        validate_fields(merged)
        edited = replace(self, fields=merged, revision=self.revision + 1)
        return edited, ConfigChange.of(
            ctx, RecordKind.WORK_SOURCE, self.name, edited.revision, ChangeOp.EDIT, changes, at
        )

    def set_retired(
        self, retired: bool, ctx: ChangeContext, *, if_match: int | None, at: datetime
    ) -> tuple[ConfiguredWorkSource, ConfigChange] | None:
        """Retire or enable; ``None`` when the record already stands there (a redundant verb writes nothing)."""
        self.require_revision(if_match)
        op = ChangeOp.RETIRE if retired else ChangeOp.ENABLE
        if self.TRANSITIONS[RecordState.of(self.retired)][op] is Verdict.NO_OP:
            return None
        moved = replace(self, revision=self.revision + 1, retired=retired)
        flip = (FieldChange(RETIRED_FIELD, self.retired, retired),)
        return moved, ConfigChange.of(ctx, RecordKind.WORK_SOURCE, self.name, moved.revision, op, flip, at)


@domain_model
@dataclass(frozen=True)
class WorkSourceEdit:
    """A sparse edit: :data:`UNSET` leaves a field, ``None`` clears a nullable one."""

    provider: str | UnsetType = UNSET
    locator: str | UnsetType = UNSET
    api_base: str | None | UnsetType = UNSET
    web_base: str | None | UnsetType = UNSET
    annotate: bool | UnsetType = UNSET
    secret: str | None | UnsetType = UNSET


def is_built_in(name: str) -> bool:
    """Whether ``name`` is the built-in hub source, which no configured record may claim or change."""
    return name == RESERVED_HUB_SOURCE_NAME


def require_configurable(name: str) -> None:
    """:class:`BuiltInWorkSource` when ``name`` is the built-in hub source."""
    if is_built_in(name):
        raise BuiltInWorkSource()


def validate_name(name: str) -> None:
    if not name or not name.strip():
        raise ConfigFieldError("name", "must not be blank")
    if ":" in name:
        # A colon breaks the ingest-token grammar's first-colon split.
        raise ConfigFieldError("name", f"{name!r} must not contain ':'")
    if is_built_in(name):
        raise ConfigFieldError("name", f"{name!r} is reserved for the built-in hub source")


def validate_fields(fields: WorkSourceFields) -> None:
    if fields.provider not in KNOWN_WORK_SOURCE_PROVIDERS:
        raise ConfigFieldError(
            "provider", f"unknown provider {fields.provider!r} (known: {sorted(KNOWN_WORK_SOURCE_PROVIDERS)})"
        )
    if fields.provider == "github" and not _GITHUB_LOCATOR.match(fields.locator):
        raise ConfigFieldError("locator", f"github locator must be owner/name, got {fields.locator!r}")
    if fields.secret is None and fields.provider in PROVIDERS_NEEDING_CREDENTIAL:
        raise ConfigFieldError("secret", f"provider {fields.provider} needs a credential")


def merge(current: WorkSourceFields, edit: WorkSourceEdit) -> WorkSourceFields:
    """``current`` with every set field of ``edit`` applied; ``None`` on a non-nullable
    field is refused naming it."""
    changes: dict[str, object] = {}
    for name in _FIELDS:
        value = getattr(edit, name)
        if value is UNSET:
            continue
        if value is None and name not in _NULLABLE:
            raise ConfigFieldError(name, "must not be null")
        changes[name] = value
    return replace(current, **changes)  # type: ignore[arg-type]


def diff(old: WorkSourceFields | None, new: WorkSourceFields) -> tuple[FieldChange, ...]:
    """The fields that differ, by wire name. With no ``old`` (a create), every set field
    is listed against ``None``."""
    changes: list[FieldChange] = []
    for name in _FIELDS:
        new_value = getattr(new, name)
        if old is None:
            if new_value is not None:
                changes.append(FieldChange(name, None, new_value))
        elif getattr(old, name) != new_value:
            changes.append(FieldChange(name, getattr(old, name), new_value))
    return tuple(changes)


class IReadWorkSourceRepository(Protocol):
    """Work-source reads. Controllers depend on this variant."""

    def get(self, name: str) -> ConfiguredWorkSource | None: ...

    def get_many(self, names: list[str]) -> dict[str, ConfiguredWorkSource]:
        """Every named source that exists, keyed by name (``bzh:bulk-reconstitution``)."""
        ...

    def list_all(self, *, include_retired: bool) -> list[ConfiguredWorkSource]:
        """Ordered by name; retired sources only when ``include_retired``."""
        ...


class IWriteWorkSourceRepository(IReadWorkSourceRepository, Protocol):
    """Work-source writes. Only :class:`ConfigAuthoring` depends on this variant; each
    method commits ``change`` in the same transaction as the record write."""

    def create(self, record: ConfiguredWorkSource, *, change: ConfigChange) -> ConfiguredWorkSource:
        """Insert. :class:`WorkSourceNameTaken` / :class:`WorkSourceLocatorTaken` on a
        collision; :class:`WorkSourceSecretUnavailable` when the secret is missing or retired."""
        ...

    def update(self, record: ConfiguredWorkSource, *, from_revision: int, change: ConfigChange) -> ConfiguredWorkSource:
        """Compare-and-set ``from_revision``, writing ``record``'s fields and revision;
        :class:`ConfigRevisionConflict` when the stored revision has moved."""
        ...

    def record_lifecycle(
        self,
        record: ConfiguredWorkSource,
        *,
        retired: bool,
        from_revision: int,
        at: datetime,
        by: str,
        change: ConfigChange,
    ) -> ConfiguredWorkSource:
        """Append the lifecycle fact and move the revision to ``record.revision``, as one
        compare-and-set on ``from_revision``. Enabling re-checks the secret."""
        ...
