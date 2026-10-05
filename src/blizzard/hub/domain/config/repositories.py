"""Repository records: the stored shape, validation, sparse merge, and the seams.

Every field is required, so an edit carrying ``None`` on any field is refused naming it.
The rows are inert: nothing reads them for delivery yet."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import ClassVar, Protocol
from urllib.parse import urlparse

from blizzard.foundation.roles import domain_model
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
from blizzard.hub.domain.config.work_sources import ConfigFieldError, ConfigRevisionConflict
from blizzard.hub.domain.kernel.unset import UNSET, UnsetType

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


@domain_model
@dataclass(frozen=True)
class RepositoryFields:
    """The mutable fields of a repository, by wire name."""

    forge_api_url: str
    owner: str
    repo: str
    base_branch: str
    secret_name: str


@domain_model
@dataclass(frozen=True)
class ConfiguredRepository:
    """A stored repository. ``retired`` derives from the newest lifecycle fact.

    Each verb returns the record to write with the :class:`ConfigChange` committed beside it,
    or ``None`` when the verb changes nothing and so writes nothing."""

    TRANSITIONS: ClassVar[Mapping[RecordState, Mapping[ChangeOp, Verdict]]] = FIELDED_RECORD_TRANSITIONS

    name: str
    fields: RepositoryFields
    revision: int
    created_at: datetime
    created_by: str
    retired: bool = False

    @classmethod
    def new(
        cls, name: str, fields: RepositoryFields, ctx: ChangeContext, *, at: datetime
    ) -> tuple[ConfiguredRepository, ConfigChange]:
        validate_name(name)
        validate_fields(fields)
        record = cls(name=name, fields=fields, revision=1, created_at=at, created_by=ctx.actor)
        return record, ConfigChange.of(ctx, RecordKind.REPOSITORY, name, 1, ChangeOp.CREATE, diff(None, fields), at)

    def require_revision(self, if_match: int | None) -> None:
        """:class:`ConfigRevisionConflict` when ``if_match`` names a revision other than the stored one."""
        if if_match is not None and if_match != self.revision:
            raise ConfigRevisionConflict("repository", self.name, current=self.revision)

    def edit(
        self, edit: RepositoryEdit, ctx: ChangeContext, *, if_match: int | None, at: datetime
    ) -> tuple[ConfiguredRepository, ConfigChange] | None:
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
            ctx, RecordKind.REPOSITORY, self.name, edited.revision, ChangeOp.EDIT, changes, at
        )

    def set_retired(
        self, retired: bool, ctx: ChangeContext, *, if_match: int | None, at: datetime
    ) -> tuple[ConfiguredRepository, ConfigChange] | None:
        """Retire or enable; ``None`` when the record already stands there (a redundant verb writes nothing)."""
        self.require_revision(if_match)
        op = ChangeOp.RETIRE if retired else ChangeOp.ENABLE
        if self.TRANSITIONS[RecordState.of(self.retired)][op] is Verdict.NO_OP:
            return None
        moved = replace(self, revision=self.revision + 1, retired=retired)
        flip = (FieldChange(RETIRED_FIELD, self.retired, retired),)
        return moved, ConfigChange.of(ctx, RecordKind.REPOSITORY, self.name, moved.revision, op, flip, at)


@domain_model
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
