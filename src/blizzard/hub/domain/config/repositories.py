"""Repository records: the stored shape, validation, sparse merge, and the seams.

A repository has the shape a project has: an immutable id every stored reference holds, a tenant-unique renameable slug
whose former values keep resolving until another record claims them, and a free display name. Every field is required,
so an edit carrying ``None`` on any field is refused naming it."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import ClassVar, NewType, Protocol
from urllib.parse import urlparse

from blizzard.foundation.ids import Id, IdPrefix
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
from blizzard.hub.domain.config.errors import ConfigFieldError, ConfigRevisionConflict
from blizzard.hub.domain.config.forges import ConfiguredForge, ForgeId, NewForge
from blizzard.hub.domain.kernel.id_or_slug import IIdOrSlugLookup, SluggedKind
from blizzard.hub.domain.kernel.id_or_slug import resolve as resolve_reference
from blizzard.hub.domain.kernel.slugs import NAME_MAX, SLUG_MAX, DisplayName, Slug
from blizzard.hub.domain.kernel.unset import UNSET, UnsetType

RepositoryId = NewType("RepositoryId", str)

#: The mutable fields, in the order a diff lists them.
_FIELDS = ("forge_id", "owner", "repo", "base_branch", "secret_name")
#: Every field a diff lists, by wire name: the record's own handle and label, then the mutable fields — the forge
#: recorded by its id under ``forge``, as every reference is held.
_DIFF_FIELDS = ("slug", "name", "forge", *_FIELDS[1:])
#: The wire name a field's change is listed under, where it differs from the field's own.
_DIFF_NAMES = {"forge": "forge_id"}


class RepositorySlugTaken(Exception):
    def __init__(self, slug: str) -> None:
        super().__init__(f"repository {slug} already exists")
        self.slug = slug


class RepositoryCoordinateTaken(Exception):
    """``(forge, owner, repo)`` already belongs to ``holder`` — retired holders keep their claim. ``forge`` is how the
    forge is named: its slug and API origin."""

    def __init__(self, forge: str, owner: str, repo: str, *, holder: str) -> None:
        super().__init__(f"{forge} {owner}/{repo} already belongs to repository {holder}; enable it instead")
        self.holder = holder


class RepositorySecretUnavailable(ConfigFieldError):
    """The referenced secret is missing or retired."""

    def __init__(self, secret: str, *, retired: bool) -> None:
        super().__init__("secret_name", f"secret {secret} is {'retired' if retired else 'unknown'}")


@domain_model
@dataclass(frozen=True)
class RepositorySlug(Slug):
    """A repository's slug, in the shared slug grammar."""

    @classmethod
    def parse(cls, raw: str) -> RepositorySlug:
        """:class:`ConfigFieldError` on ``slug`` when ``raw`` is outside the slug alphabet or length."""
        slug = cls.of(raw)
        if slug is None:
            raise ConfigFieldError(
                "slug",
                f"must be 1-{SLUG_MAX} lowercase letters, digits and single inner hyphens, got {raw!r}",
            )
        return slug


@domain_model
@dataclass(frozen=True)
class RepositoryName(DisplayName):
    """A repository's display name, in the shared display-name grammar."""

    @classmethod
    def parse(cls, raw: str) -> RepositoryName:
        """:class:`ConfigFieldError` on ``name`` when ``raw`` is blank, too long, or padded."""
        name = cls.of(raw)
        if name is None:
            raise ConfigFieldError(
                "name", f"must be 1-{NAME_MAX} characters with no leading or trailing whitespace, got {raw!r}"
            )
        return name


#: Repositories as the shared id-or-slug resolution sees them.
REPOSITORY_KIND = SluggedKind(IdPrefix.REPOSITORY, RepositoryId, RepositorySlug.of)

#: The reads a resolution needs; :class:`IReadRepositoryRecordRepository` is one.
type IRepositoryLookup = IIdOrSlugLookup[RepositoryId, RepositorySlug, ConfiguredRepository]


@domain_model
@dataclass(frozen=True)
class RepositoryFields:
    """The mutable fields of a repository: the forge it lives on, by id, and its coordinate and credential there."""

    forge_id: ForgeId
    owner: str
    repo: str
    base_branch: str
    secret_name: str


@domain_model
@dataclass(frozen=True)
class RepositoryDraft:
    """A repository's fields before its forge is resolved — every field but the forge."""

    owner: str
    repo: str
    base_branch: str
    secret_name: str

    def on(self, forge_id: ForgeId) -> RepositoryFields:
        """The fields of a repository on the forge ``forge_id``."""
        return RepositoryFields(forge_id, self.owner, self.repo, self.base_branch, self.secret_name)


@domain_model
@dataclass(frozen=True)
class ConfiguredRepository:
    """A stored repository. ``retired`` derives from the newest lifecycle fact. Nothing references a repository by its
    ``slug`` or ``name``; a repository's change rows are keyed by its id.

    Each verb returns the record to write with the :class:`ConfigChange` committed beside it,
    or ``None`` when the verb changes nothing and so writes nothing."""

    TRANSITIONS: ClassVar[Mapping[RecordState, Mapping[ChangeOp, Verdict]]] = FIELDED_RECORD_TRANSITIONS

    repository_id: RepositoryId
    slug: str
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
        cls,
        slug: str,
        name: str | None,
        fields: RepositoryFields,
        ctx: ChangeContext,
        *,
        at: datetime,
        repository_id: RepositoryId | None = None,
    ) -> tuple[ConfiguredRepository, ConfigChange]:
        """A new record at revision 1; a ``name`` of ``None`` is the slug. ``repository_id`` is minted when not given —
        a document apply mints the id of a record it creates before it plans the links to it."""
        parsed = RepositorySlug.parse(slug)
        label = parsed.value if name is None else RepositoryName.parse(name).value
        validate_fields(fields)
        record = cls(
            repository_id=repository_id or RepositoryId(Id.mint_at(IdPrefix.REPOSITORY, at).value),
            slug=parsed.value,
            name=label,
            fields=fields,
            revision=1,
            created_at=at,
            created_by=ctx.actor,
        )
        return record, record._change(ctx, ChangeOp.CREATE, diff(None, record), at)

    def require_revision(self, if_match: int | None) -> None:
        """:class:`ConfigRevisionConflict` when ``if_match`` names a revision other than the stored one."""
        if if_match is not None and if_match != self.revision:
            raise ConfigRevisionConflict("repository", self.repository_id, current=self.revision)

    def edit(
        self, edit: RepositoryEdit, ctx: ChangeContext, *, if_match: int | None, at: datetime
    ) -> tuple[ConfiguredRepository, ConfigChange] | None:
        """Apply a sparse edit, legal from either state. The revision check comes first, even
        for an edit that changes nothing; an empty diff never re-validates the stored fields."""
        self.require_revision(if_match)
        slug, name = merge_labels(self, edit)
        edited = replace(self, slug=slug, name=name, fields=merge(self.fields, edit))
        changes = diff(self, edited)
        if not changes:
            return None
        validate_fields(edited.fields)
        edited = replace(edited, revision=self.revision + 1)
        return edited, edited._change(ctx, ChangeOp.EDIT, changes, at)

    def ref(self) -> RepositoryRef:
        return RepositoryRef(self.repository_id, self.slug)

    def slug_left_for(self, edited: ConfiguredRepository) -> str | None:
        """The slug this record leaves when ``edited`` replaces it — ``None`` when the slug stays."""
        return self.slug if edited.slug != self.slug else None

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
        return moved, moved._change(ctx, op, flip, at)

    def _change(self, ctx: ChangeContext, op: ChangeOp, changes: tuple[FieldChange, ...], at: datetime) -> ConfigChange:
        return ConfigChange.of(ctx, RecordKind.REPOSITORY, self.repository_id, self.revision, op, changes, at)


@domain_model
@dataclass(frozen=True)
class RepositoryRef:
    """A repository as a reference resolves it: its id, and the slug it currently answers to."""

    repository_id: RepositoryId
    slug: str


@domain_model
@dataclass(frozen=True)
class RepositoryEdit:
    """A sparse edit: :data:`UNSET` leaves a field; ``None`` is refused on every field."""

    slug: str | None | UnsetType = UNSET
    name: str | None | UnsetType = UNSET
    forge_id: ForgeId | None | UnsetType = UNSET
    owner: str | None | UnsetType = UNSET
    repo: str | None | UnsetType = UNSET
    base_branch: str | None | UnsetType = UNSET
    secret_name: str | None | UnsetType = UNSET


@domain_model
@dataclass(frozen=True)
class ResolvedRepository:
    """The repository record one commit pointer resolved to — its forge's id, owner, base branch and
    secret, and the record's own id and current slug."""

    forge_id: ForgeId
    owner: str
    base_branch: str
    secret_name: str
    record: str = ""
    slug: str = ""


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
    NOT_LINKED = "repository-not-linked"


@domain_model
@dataclass(frozen=True)
class RepositoryResolution:
    """The outcome of resolving a chunk's commit pointers. ``targets`` holds one resolved record per
    pointer, in origin order, exactly when ``outcome`` is :attr:`RepositoryOutcome.RESOLVED`;
    ``qualified`` maps each origin's index to the ``owner/repo`` it resolved to, and ``records`` to
    its repository record's id; ``forges`` holds each forge a target names, once."""

    outcome: RepositoryOutcome
    targets: tuple[ResolvedRepository, ...] = ()
    qualified: tuple[str, ...] = ()
    records: tuple[str, ...] = ()
    detail: str = ""
    forges: tuple[ConfiguredForge, ...] = ()

    def forge_of(self, target: ResolvedRepository) -> ConfiguredForge:
        """The forge ``target`` lives on."""
        return next(forge for forge in self.forges if forge.forge_id == target.forge_id)


def _host(origin: str) -> str:
    """An address's host, with its port when it has one — GitHub's ``api.`` prefix is not part of its web host."""
    return urlparse(origin).netloc.rpartition("@")[2].lower().removeprefix("api.")


def _forge_hosts(forge: ConfiguredForge | None) -> frozenset[str]:
    """The hosts a commit origin on ``forge`` may name: its web host and its API host."""
    if forge is None:
        return frozenset()
    return frozenset({_host(forge.web_origin), _host(forge.fields.api_base)})


type ForgesById = Mapping[str, ConfiguredForge]


def forges_by_id(forges: Iterable[ConfiguredForge]) -> dict[str, ConfiguredForge]:
    """``forges`` keyed by id, as resolution reads them."""
    return {forge.forge_id: forge for forge in forges}


def _candidates(
    origin: CommitOrigin, rows: list[ConfiguredRepository], forges: ForgesById
) -> list[ConfiguredRepository]:
    found = [row for row in rows if row.fields.repo == origin.name]
    if origin.owner:
        found = [row for row in found if row.fields.owner.lower() == origin.owner.lower()]
    if origin.host:
        host = origin.host.lower().removeprefix("api.")
        found = [row for row in found if host in _forge_hosts(forges.get(row.fields.forge_id))]
    return found


def _standing(rows: list[ConfiguredRepository], minted_at: datetime) -> list[ConfiguredRepository]:
    """The records standing for a chunk minted at ``minted_at``: enabled, or retired after it."""
    return [row for row in rows if row.retired_at is None or row.retired_at > minted_at]


def _linked(rows: list[ConfiguredRepository], linked: frozenset[str] | None) -> list[ConfiguredRepository]:
    """``rows`` narrowed to the record ids in ``linked``; every row when ``linked`` is ``None``."""
    return rows if linked is None else [row for row in rows if row.repository_id in linked]


def _label(origin: CommitOrigin) -> str:
    return f"{origin.owner}/{origin.name}" if origin.owner else origin.name


def _refusal(
    origin: CommitOrigin,
    found: list[ConfiguredRepository],
    standing: list[ConfiguredRepository],
    forges: ForgesById,
    *,
    linked: frozenset[str] | None,
    project: str,
) -> RepositoryResolution:
    """Why ``origin`` matched other than exactly one record of ``found``, its candidates within ``linked``. A miss
    that matches exactly one standing record of the tenant is that record not being linked; any other miss is
    unresolved, an ambiguity naming the records it is between."""
    label = _label(origin)
    if found:
        slugs = ", ".join(sorted(row.slug for row in found))
        return RepositoryResolution(
            RepositoryOutcome.UNRESOLVED, detail=f"{label} is ambiguous between repository records {slugs}"
        )
    tenant_wide = [] if linked is None else _candidates(origin, standing, forges)
    if len(tenant_wide) == 1:
        return RepositoryResolution(
            RepositoryOutcome.NOT_LINKED,
            detail=f"{label} resolves to repository record {tenant_wide[0].slug}, which project {project} doesn't link",
        )
    if tenant_wide:
        slugs = ", ".join(sorted(row.slug for row in tenant_wide))
        return RepositoryResolution(
            RepositoryOutcome.UNRESOLVED,
            detail=f"{label} is ambiguous between repository records {slugs}, none of which project {project} links",
        )
    return RepositoryResolution(RepositoryOutcome.UNRESOLVED, detail=f"no repository record stands for {label}")


def resolve_chunk_repositories(
    origins: list[CommitOrigin],
    rows: list[ConfiguredRepository],
    forges: Iterable[ConfiguredForge],
    *,
    minted_at: datetime,
    linked: frozenset[str] | None,
    project: str = "",
) -> RepositoryResolution:
    """Resolve each commit pointer to its own repository record.

    A pointer matches a record by its bare name, narrowed by the origin's owner and forge host
    when it encodes them — the web or API host of the record's forge, among ``forges``, retired ones
    included. Only records standing for the chunk count: an enabled one, or one retired after the
    chunk was minted — and of those, only the ones ``linked`` names, the chunk's project's links;
    ``None`` narrows nothing. Each pointer narrows on its own, so a name ambiguous across the tenant
    resolves when its project links one of them. ``project`` is the
    label a not-linked refusal names. No pointers is not a refusal — there is nothing to land."""
    if not origins:
        return RepositoryResolution(RepositoryOutcome.NOTHING_TO_RESOLVE)
    by_id = forges_by_id(forges)
    standing = _standing(rows, minted_at)
    candidates = _linked(standing, linked)
    picked: list[ConfiguredRepository] = []
    for origin in origins:
        found = _candidates(origin, candidates, by_id)
        if len(found) != 1:
            return _refusal(origin, found, standing, by_id, linked=linked, project=project)
        picked.append(found[0])
    return RepositoryResolution(
        RepositoryOutcome.RESOLVED,
        targets=tuple(
            ResolvedRepository(
                row.fields.forge_id,
                row.fields.owner,
                row.fields.base_branch,
                row.fields.secret_name,
                row.repository_id,
                row.slug,
            )
            for row in picked
        ),
        qualified=tuple(f"{row.fields.owner}/{row.fields.repo}" for row in picked),
        records=tuple(row.repository_id for row in picked),
        forges=tuple({row.fields.forge_id: by_id[row.fields.forge_id] for row in picked}.values()),
    )


def resolve_origin_record(
    origin: CommitOrigin,
    rows: list[ConfiguredRepository],
    forges: Iterable[ConfiguredForge],
    *,
    minted_at: datetime,
    linked: frozenset[str] | None,
) -> ConfiguredRepository | None:
    """The one record ``origin`` resolves to by :func:`resolve_chunk_repositories`'s rules for a
    chunk minted at ``minted_at`` whose project links ``linked``, or ``None`` — unmatched,
    ambiguous, or not linked."""
    found = _candidates(origin, _linked(_standing(rows, minted_at), linked), forges_by_id(forges))
    return found[0] if len(found) == 1 else None


def resolve_repository(
    origin: CommitOrigin,
    rows: list[ConfiguredRepository],
    forges: Iterable[ConfiguredForge],
    *,
    linked: frozenset[str] | None,
) -> ConfiguredRepository | None:
    """The single enabled record ``origin`` names among those ``linked`` names (every record
    when ``None``), or ``None`` — unmatched or ambiguous. A point-in-time lookup with no chunk
    to date it by, so retired records never match."""
    found = _candidates(origin, _linked([row for row in rows if row.retired_at is None], linked), forges_by_id(forges))
    return found[0] if len(found) == 1 else None


def validate_fields(fields: RepositoryFields) -> None:
    for name in _FIELDS:
        value = getattr(fields, name)
        if not value or not value.strip():
            raise ConfigFieldError("forge" if name == "forge_id" else name, "must not be blank")


def merge(current: RepositoryFields, edit: RepositoryEdit) -> RepositoryFields:
    """``current`` with every set field of ``edit`` applied; ``None`` is refused naming the field."""
    return replace(
        current,
        forge_id=_merged_required("forge", current.forge_id, edit.forge_id),
        owner=_merged_required("owner", current.owner, edit.owner),
        repo=_merged_required("repo", current.repo, edit.repo),
        base_branch=_merged_required("base_branch", current.base_branch, edit.base_branch),
        secret_name=_merged_required("secret_name", current.secret_name, edit.secret_name),
    )


def _merged_required[T](name: str, current: T, value: T | None | UnsetType) -> T:
    if value is UNSET:
        return current
    if value is None:
        raise ConfigFieldError(name, "must not be null")
    return value


def merge_labels(current: ConfiguredRepository, edit: RepositoryEdit) -> tuple[str, str]:
    """The slug and name ``current`` holds once ``edit`` applies; a set one is validated whether or not it differs,
    and ``None`` is refused naming the field."""
    return (
        _merged_label("slug", current.slug, edit.slug, RepositorySlug.parse),
        _merged_label("name", current.name, edit.name, RepositoryName.parse),
    )


def _merged_label(
    field: str, held: str, value: str | None | UnsetType, parse: Callable[[str], Slug | DisplayName]
) -> str:
    if value is UNSET:
        return held
    if value is None:
        raise ConfigFieldError(field, "must not be null")
    return parse(value).value


def diff(old: ConfiguredRepository | None, new: ConfiguredRepository) -> tuple[FieldChange, ...]:
    """The fields that differ, by wire name. With no ``old`` (a create), every field is
    listed against ``None``."""
    changes: list[FieldChange] = []
    for name in _DIFF_FIELDS:
        new_value = _field(new, name)
        old_value = None if old is None else _field(old, name)
        if old_value != new_value:
            changes.append(FieldChange(name, old_value, new_value))
    return tuple(changes)


def _field(record: ConfiguredRepository, name: str) -> str:
    return getattr(record if name in ("slug", "name") else record.fields, _DIFF_NAMES.get(name, name))


class IReadRepositoryRecordRepository(Protocol):
    """Read-only repository-record operations."""

    def get(self, repository_id: RepositoryId) -> ConfiguredRepository | None: ...

    def get_many(self, repository_ids: list[RepositoryId]) -> dict[RepositoryId, ConfiguredRepository]:
        """Every named repository that exists, keyed by id (``bzh:bulk-reconstitution``)."""
        ...

    def list_all(self, *, include_retired: bool) -> list[ConfiguredRepository]:
        """Ordered by slug; retired repositories only when ``include_retired``."""
        ...

    def get_by_slug(self, slug: RepositorySlug) -> ConfiguredRepository | None:
        """The repository whose current slug is ``slug``."""
        ...

    def get_by_former_slug(self, slug: RepositorySlug) -> ConfiguredRepository | None:
        """The repository that left ``slug``, while no repository holds it again."""
        ...

    def resolve(self, value: str) -> ConfiguredRepository | None:
        """The repository an id or slug names (:func:`resolve_repository_reference`)."""
        ...


def resolve_repository_reference(value: str, repositories: IRepositoryLookup) -> ConfiguredRepository | None:
    """The repository ``value`` names — an id, a current slug, or a slug it left — or ``None``."""
    return resolve_reference(value, REPOSITORY_KIND, repositories)


class IWriteRepositoryRecordRepository(IReadRepositoryRecordRepository, Protocol):
    """Adds the repository-record writes; each method commits ``change`` in the same
    transaction as the record write."""

    def create(
        self, record: ConfiguredRepository, *, change: ConfigChange, forge: NewForge | None = None
    ) -> ConfiguredRepository:
        """Insert, taking ``record.slug`` from any repository that left it, after creating ``forge``, when given, in the
        same transaction. :class:`RepositorySlugTaken` / :class:`RepositoryCoordinateTaken` on a collision;
        :class:`RepositorySecretUnavailable` when the secret is missing or retired; :class:`ForgeOriginTaken` /
        :class:`ForgeSlugTaken` when another forge holds ``forge``'s origin or slug."""
        ...

    def update(
        self,
        record: ConfiguredRepository,
        *,
        from_revision: int,
        left_slug: str | None,
        change: ConfigChange,
        forge: NewForge | None = None,
    ) -> ConfiguredRepository:
        """Compare-and-set ``from_revision``, writing ``record``'s fields and revision — after creating ``forge``, when
        given, in the same transaction, :class:`ForgeOriginTaken` or :class:`ForgeSlugTaken` when another forge holds
        its origin or slug;
        :class:`ConfigRevisionConflict` when the stored revision has moved. When ``left_slug`` is set the slug moves in
        the same transaction: the former-slug row holding the new slug is removed and ``left_slug`` becomes this
        repository's former slug at ``change.recorded_at``. :class:`RepositorySlugTaken` when another repository holds
        the new slug as its current one."""
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
