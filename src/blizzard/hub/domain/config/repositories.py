"""Repository records: the stored shape, validation, sparse merge, and the seams.

Every field is required, so an edit carrying ``None`` on any field is refused naming it.
The rows are inert: nothing reads them for delivery yet."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol
from urllib.parse import urlparse

from blizzard.foundation.roles import dto
from blizzard.hub.domain.config.changes import ConfigChange, FieldChange
from blizzard.hub.domain.config.work_sources import ConfigFieldError
from blizzard.hub.domain.edit import UNSET, UnsetType

#: The mutable fields, in the order a diff lists them.
_FIELDS = ("forge_api_url", "owner", "repo", "base_branch", "secret_name")


class RepositoryNameTaken(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(f"repository {name} already exists")
        self.name = name


class RepositoryCoordinateTaken(Exception):
    """``(forge_api_url, owner, repo)`` already belongs to ``holder`` — retired holders keep their claim."""

    def __init__(self, forge_api_url: str, owner: str, repo: str, *, holder: str) -> None:
        super().__init__(f"{forge_api_url} {owner}/{repo} already belongs to repository {holder}; enable it instead")
        self.holder = holder


class RepositorySecretUnavailable(ConfigFieldError):
    """The referenced secret is missing or retired."""

    def __init__(self, secret: str, *, retired: bool) -> None:
        super().__init__("secret_name", f"secret {secret} is {'retired' if retired else 'unknown'}")


@dto
@dataclass(frozen=True)
class RepositoryFields:
    """The mutable fields of a repository, by wire name."""

    forge_api_url: str
    owner: str
    repo: str
    base_branch: str
    secret_name: str


@dto
@dataclass(frozen=True)
class ConfiguredRepository:
    """A stored repository. ``retired`` derives from the newest lifecycle fact."""

    name: str
    fields: RepositoryFields
    revision: int
    created_at: datetime
    created_by: str
    retired: bool = False


@dto
@dataclass(frozen=True)
class RepositoryEdit:
    """A sparse edit: :data:`UNSET` leaves a field; ``None`` is refused on every field."""

    forge_api_url: str | None | UnsetType = UNSET
    owner: str | None | UnsetType = UNSET
    repo: str | None | UnsetType = UNSET
    base_branch: str | None | UnsetType = UNSET
    secret_name: str | None | UnsetType = UNSET


def validate_name(name: str) -> None:
    if not name or not name.strip():
        raise ConfigFieldError("name", "must not be blank")


def validate_fields(fields: RepositoryFields) -> None:
    for name in _FIELDS:
        value = getattr(fields, name)
        if not value or not value.strip():
            raise ConfigFieldError(name, "must not be blank")
    parsed = urlparse(fields.forge_api_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ConfigFieldError("forge_api_url", f"must be an absolute http(s) URL, got {fields.forge_api_url!r}")


def merge(current: RepositoryFields, edit: RepositoryEdit) -> RepositoryFields:
    """``current`` with every set field of ``edit`` applied; ``None`` is refused naming the field."""
    changes: dict[str, object] = {}
    for name in _FIELDS:
        value = getattr(edit, name)
        if value is UNSET:
            continue
        if value is None:
            raise ConfigFieldError(name, "must not be null")
        changes[name] = value
    return replace(current, **changes)  # type: ignore[arg-type]


def diff(old: RepositoryFields | None, new: RepositoryFields) -> tuple[FieldChange, ...]:
    """The fields that differ, by wire name. With no ``old`` (a create), every field is
    listed against ``None``."""
    changes: list[FieldChange] = []
    for name in _FIELDS:
        new_value = getattr(new, name)
        old_value = None if old is None else getattr(old, name)
        if old_value != new_value:
            changes.append(FieldChange(name, old_value, new_value))
    return tuple(changes)


class IReadRepositoryRecordRepository(Protocol):
    """Repository-record reads. Controllers depend on this variant."""

    def get(self, name: str) -> ConfiguredRepository | None: ...

    def get_many(self, names: list[str]) -> dict[str, ConfiguredRepository]:
        """Every named repository that exists, keyed by name (``bzh:bulk-reconstitution``)."""
        ...

    def list_all(self, *, include_retired: bool) -> list[ConfiguredRepository]:
        """Ordered by name; retired repositories only when ``include_retired``."""
        ...


class IWriteRepositoryRecordRepository(IReadRepositoryRecordRepository, Protocol):
    """Repository-record writes. Only :class:`ConfigAuthoring` depends on this variant; each
    method commits ``change`` in the same transaction as the record write."""

    def create(self, record: ConfiguredRepository, *, change: ConfigChange) -> ConfiguredRepository:
        """Insert. :class:`RepositoryNameTaken` / :class:`RepositoryCoordinateTaken` on a
        collision; :class:`RepositorySecretUnavailable` when the secret is missing or retired."""
        ...

    def update(self, record: ConfiguredRepository, *, from_revision: int, change: ConfigChange) -> ConfiguredRepository:
        """Compare-and-set ``from_revision``, writing ``record``'s fields and revision;
        :class:`ConfigRevisionConflict` when the stored revision has moved."""
        ...

    def record_lifecycle(
        self,
        record: ConfiguredRepository,
        *,
        retired: bool,
        from_revision: int,
        at: datetime,
        by: str,
        change: ConfigChange,
    ) -> ConfiguredRepository:
        """Append the lifecycle fact and move the revision to ``record.revision``, as one
        compare-and-set on ``from_revision``. Enabling re-checks the secret."""
        ...
