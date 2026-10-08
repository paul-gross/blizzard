"""Repository records: the stored shape, validation, sparse merge, and the seams.

Every field is required, so an edit carrying ``None`` on any field is refused naming it."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import ClassVar, Protocol
from urllib.parse import urlparse

from blizzard.foundation.repo_ref import RepoRef
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
    #: When the newest retirement fact was set; ``None`` while the record is enabled.
    retired_at: datetime | None = None

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


@domain_model
@dataclass(frozen=True)
class ResolvedRepository:
    """The one repository record a chunk's commits resolved to — its forge, owner, base branch
    and secret."""

    forge_api_url: str
    owner: str
    base_branch: str
    secret_name: str


@domain_model
@dataclass(frozen=True)
class CommitOrigin:
    """A commit pointer's repository as delivery knows it: its bare ``name`` and the origin
    coordinate it encodes, if any (``host`` and ``owner`` are empty for a ``file://`` origin)."""

    name: str
    host: str = ""
    owner: str = ""

    @classmethod
    def of(cls, origin_url: str | None, name: str) -> CommitOrigin:
        """``name`` may itself be ``owner/name``; the origin's own coordinate wins when it has one."""
        ref = RepoRef.parse(origin_url) if origin_url else None
        if ref is not None:
            return cls(name=ref.name, host=ref.host, owner=ref.owner)
        owner, _, bare = name.rpartition("/")
        return cls(name=bare, owner=owner)


class RepositoryOutcome(StrEnum):
    RESOLVED = "resolved"
    NOTHING_TO_RESOLVE = "nothing-to-resolve"
    UNRESOLVED = "repository-unresolved"
    DISAGREE = "repositories-disagree"


@domain_model
@dataclass(frozen=True)
class RepositoryResolution:
    """The outcome of resolving a chunk's commit pointers. ``target`` is set exactly when
    ``outcome`` is :attr:`RepositoryOutcome.RESOLVED`; ``qualified`` maps each origin's
    index to the ``owner/repo`` it resolved to."""

    outcome: RepositoryOutcome
    target: ResolvedRepository | None = None
    qualified: tuple[str, ...] = ()
    detail: str = ""


def _forge_host(forge_api_url: str) -> str:
    """The host the forge's API fronts — GitHub's ``api.`` prefix is not part of its web host."""
    host = urlparse(forge_api_url).netloc.rpartition("@")[2].lower()
    return host.removeprefix("api.")


def _candidates(origin: CommitOrigin, rows: list[ConfiguredRepository]) -> list[ConfiguredRepository]:
    found = [row for row in rows if row.fields.repo == origin.name]
    if origin.owner:
        found = [row for row in found if row.fields.owner.lower() == origin.owner.lower()]
    if origin.host:
        found = [
            row for row in found if _forge_host(row.fields.forge_api_url) == origin.host.lower().removeprefix("api.")
        ]
    return found


def resolve_chunk_repositories(
    origins: list[CommitOrigin], rows: list[ConfiguredRepository], *, minted_at: datetime
) -> RepositoryResolution:
    """Resolve each commit pointer to a repository record, then require they all agree.

    A pointer matches a record by its bare name, narrowed by the origin's owner and forge host
    when it encodes them. Only records standing for the chunk count: an enabled one, or one
    retired after the chunk was minted. No pointers is not a refusal — there is nothing to land."""
    if not origins:
        return RepositoryResolution(RepositoryOutcome.NOTHING_TO_RESOLVE)
    standing = [row for row in rows if row.retired_at is None or row.retired_at > minted_at]
    picked: list[ConfiguredRepository] = []
    for origin in origins:
        found = _candidates(origin, standing)
        label = f"{origin.owner}/{origin.name}" if origin.owner else origin.name
        if not found:
            return RepositoryResolution(RepositoryOutcome.UNRESOLVED, detail=f"no repository record stands for {label}")
        if len(found) > 1:
            names = ", ".join(sorted(row.name for row in found))
            return RepositoryResolution(
                RepositoryOutcome.UNRESOLVED, detail=f"{label} is ambiguous between repository records {names}"
            )
        picked.append(found[0])
    first = picked[0].fields
    for row in picked[1:]:
        f = row.fields
        for label, mine, theirs in (
            ("forge_api_url", first.forge_api_url, f.forge_api_url),
            ("owner", first.owner, f.owner),
            ("base_branch", first.base_branch, f.base_branch),
            ("secret_name", first.secret_name, f.secret_name),
        ):
            if mine != theirs:
                return RepositoryResolution(
                    RepositoryOutcome.DISAGREE,
                    detail=f"repositories {picked[0].name} and {row.name} disagree on {label}: {mine!r} vs {theirs!r}",
                )
    return RepositoryResolution(
        RepositoryOutcome.RESOLVED,
        target=ResolvedRepository(first.forge_api_url, first.owner, first.base_branch, first.secret_name),
        qualified=tuple(f"{row.fields.owner}/{row.fields.repo}" for row in picked),
    )


def resolve_repository(origin: CommitOrigin, rows: list[ConfiguredRepository]) -> ConfiguredRepository | None:
    """The single enabled record ``origin`` names, or ``None`` — unmatched or ambiguous. A
    point-in-time lookup with no chunk to date it by, so retired records never match."""
    found = _candidates(origin, [row for row in rows if row.retired_at is None])
    return found[0] if len(found) == 1 else None


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
    """Read-only repository-record operations."""

    def get(self, name: str) -> ConfiguredRepository | None: ...

    def get_many(self, names: list[str]) -> dict[str, ConfiguredRepository]:
        """Every named repository that exists, keyed by name (``bzh:bulk-reconstitution``)."""
        ...

    def list_all(self, *, include_retired: bool) -> list[ConfiguredRepository]:
        """Ordered by name; retired repositories only when ``include_retired``."""
        ...


class IWriteRepositoryRecordRepository(IReadRepositoryRecordRepository, Protocol):
    """Adds the repository-record writes; each method commits ``change`` in the same
    transaction as the record write."""

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
