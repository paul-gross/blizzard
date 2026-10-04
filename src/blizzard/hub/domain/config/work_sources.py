"""Work-source records: the stored shape, validation, sparse merge, and the seams."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol

from blizzard.foundation.roles import dto
from blizzard.hub.config import KNOWN_WORK_SOURCE_PROVIDERS, RESERVED_HUB_SOURCE_NAME
from blizzard.hub.domain.config.changes import ConfigChange, FieldChange
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


@dto
@dataclass(frozen=True)
class WorkSourceFields:
    """The mutable fields of a work source, by wire name."""

    provider: str
    locator: str
    api_base: str | None
    web_base: str | None
    annotate: bool
    secret: str | None


@dto
@dataclass(frozen=True)
class ConfiguredWorkSource:
    """A stored work source. ``retired`` derives from the newest lifecycle fact."""

    name: str
    fields: WorkSourceFields
    revision: int
    created_at: datetime
    created_by: str
    retired: bool = False


@dto
@dataclass(frozen=True)
class WorkSourceEdit:
    """A sparse edit: :data:`UNSET` leaves a field, ``None`` clears a nullable one."""

    provider: str | UnsetType = UNSET
    locator: str | UnsetType = UNSET
    api_base: str | None | UnsetType = UNSET
    web_base: str | None | UnsetType = UNSET
    annotate: bool | UnsetType = UNSET
    secret: str | None | UnsetType = UNSET


def validate_name(name: str) -> None:
    if not name or not name.strip():
        raise ConfigFieldError("name", "must not be blank")
    if ":" in name:
        # A colon breaks the ingest-token grammar's first-colon split.
        raise ConfigFieldError("name", f"{name!r} must not contain ':'")
    if name == RESERVED_HUB_SOURCE_NAME:
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
