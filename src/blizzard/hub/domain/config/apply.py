"""Declarative apply — a document reconciled against the stored records (``bzh:config-apply``).

An apply is a door and not an owner: it decides, per named record, the same creates, edits and enables the
per-kind verbs decide, and hands the whole ordered plan to :class:`IConfigApplyWriter` to commit as one
transaction. It never retires a record the document leaves out. A record the document omits a field of keeps
that field (sparse merge, ``bzh:configured-record``); a record that already stands as declared writes nothing.

The forges the entries name by an old address field that no forge holds are planned beside them, one per origin, and
commit first, each attributed to the first entry naming it.

A section of a kind owned by a package that builds on this one — the ``projects`` section — is planned by its owner
and handed in already planned; its writes commit after the repositories and before the scopes."""

from __future__ import annotations

from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar, Protocol, Self

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.changes import (
    ChangeContext,
    ChangeOp,
    ConfigChange,
    FieldChange,
    RecordKind,
    RecordState,
)
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.config.forges import ConfiguredForge
from blizzard.hub.domain.config.repositories import (
    ConfiguredRepository,
    RepositoryEdit,
    RepositoryFields,
    RepositoryId,
)
from blizzard.hub.domain.config.work_sources import (
    BuiltInWorkSource,
    ConfiguredWorkSource,
    WorkSourceEdit,
    WorkSourceFields,
    WorkSourceId,
)

PROJECTS_SECTION = "projects"
SECRETS_SECTION = "secrets"
WORK_SOURCES_SECTION = "work_sources"
REPOSITORIES_SECTION = "repositories"
SCOPES_SECTION = "scopes"
ROUTINES_SECTION = "routines"


class SecretNotActive(ConfigFieldError):
    """A secret the document lists is missing or retired; the entry itself is the location, so no field is named."""

    def __init__(self, secret: str, *, retired: bool) -> None:
        super().__init__("", f"secret {secret} is {'retired' if retired else 'unknown'}")


class ApplyEntryRefused(Exception):
    """The apply is refused at one document entry; ``cause`` is the refusal the per-kind verb would raise."""

    def __init__(self, section: str, index: int, cause: Exception) -> None:
        super().__init__(f"{section}[{index}]: {cause}")
        self.section = section
        self.index = index
        self.cause = cause


@domain_model
@dataclass(frozen=True)
class WorkSourceDeclaration:
    """A work source the document names by slug: its create fields, the display name it states (``None`` leaves a
    stored record's, and a new one's is the slug), the sparse edit of only the fields it states, and the id a record
    the document creates takes — minted at the edge, so the projects section can link to it."""

    slug: str
    name: str | None
    fields: WorkSourceFields
    edit: WorkSourceEdit
    work_source_id: WorkSourceId

    @property
    def key(self) -> str:
        return self.slug


@domain_model
@dataclass(frozen=True)
class RepositoryDeclaration:
    """A repository the document names by slug: its create fields, the display name it states (``None`` leaves a stored
    record's, and a new one's is the slug), the sparse edit of only the fields it states, and the id a record the
    document creates takes — minted at the edge, so the projects section can link to it."""

    slug: str
    name: str | None
    fields: RepositoryFields
    edit: RepositoryEdit
    repository_id: RepositoryId

    @property
    def key(self) -> str:
        return self.slug


class DeclarableRecord(Protocol):
    """A configured record of a kind owned outside this package, reconciled by its own verbs. Each verb
    returns the record to write with its change, or ``None`` when it changes nothing."""

    @property
    def name(self) -> str: ...

    @property
    def revision(self) -> int: ...

    @property
    def retired(self) -> bool: ...

    def set_retired(
        self, retired: bool, ctx: ChangeContext, *, if_match: int | None, at: datetime
    ) -> tuple[DeclarableRecord, ConfigChange] | None: ...


class ProjectRecord(Protocol):
    """A project, as the writer of an apply reads it: a project is created or edited, never retired, so it is no
    :class:`DeclarableRecord`."""

    @property
    def project_id(self) -> str: ...

    @property
    def slug(self) -> str: ...

    @property
    def revision(self) -> int: ...


class LinkRecord(Protocol):
    """A project's link to a work source or a repository, as the writers of an apply and of an import read it:
    ``target`` is the linked record's id, and a link is unlinked while ``linked`` is false."""

    KIND: ClassVar[RecordKind]
    LABEL: ClassVar[str]

    @property
    def project_id(self) -> str: ...

    @property
    def revision(self) -> int: ...

    @property
    def linked(self) -> bool: ...

    def target(self) -> str: ...


#: A record of any kind an apply writes, as :class:`PlannedWrite` carries it.
WrittenRecord = (
    ConfiguredForge | ConfiguredWorkSource | ConfiguredRepository | DeclarableRecord | ProjectRecord | LinkRecord
)


@domain_model
@dataclass(frozen=True)
class PlannedForge:
    """A forge an apply creates for an origin its entries name that no forge holds, attributed to the first entry —
    ``section`` and ``index`` — that names it."""

    section: str
    index: int
    record: ConfiguredForge
    change: ConfigChange


@domain_model
@dataclass(frozen=True)
class DeclaredReferences:
    """What an entry's reference checks read: every ``(project id, slug)`` of a scope stored or declared in the
    document, the graph names that resolve to an enabled graph, and each stored routine's linked scope set by its
    ``(project id, name)``."""

    scopes: frozenset[tuple[str, str]] = frozenset()
    enabled_graphs: frozenset[str] = frozenset()
    linked_scopes: Mapping[tuple[str, str], tuple[str, ...]] = field(default_factory=dict)


class RecordDeclaration(Protocol):
    """An entry of a kind owned outside this package: the ``key`` its stored record is found by, its reference
    ``check``, the record it ``create``s, the sparse edit of the fields it states (``edit_record``), and any write
    that ``settle``s the record afterwards."""

    @property
    def name(self) -> str: ...

    @property
    def key(self) -> tuple[str, str]:
        """The ``(project id, name)`` that tells the entry's record apart from every other of its kind."""
        ...

    def check(self, refs: DeclaredReferences, stored: DeclarableRecord | None) -> None:
        """:class:`ConfigFieldError` naming the field whose reference does not resolve."""
        ...

    def create(self, ctx: ChangeContext, *, at: datetime) -> tuple[DeclarableRecord, ConfigChange]: ...

    def edit_record(
        self, record: DeclarableRecord, ctx: ChangeContext, *, at: datetime
    ) -> tuple[DeclarableRecord, ConfigChange] | None:
        """Apply the entry's sparse edit to the stored ``record``."""
        ...

    def settle(
        self, record: DeclarableRecord, refs: DeclaredReferences, ctx: ChangeContext, *, at: datetime
    ) -> tuple[DeclarableRecord, ConfigChange] | None: ...


@domain_model
@dataclass(frozen=True)
class PlannedWrite:
    """One record write the apply will commit: the record after the write, the
    revision it moves from (``None`` for a create) and the change row committed beside it."""

    section: str
    index: int
    record: WrittenRecord
    from_revision: int | None
    change: ConfigChange


@domain_model
@dataclass(frozen=True)
class EntryOutcome:
    """One outcome row: a change written, or ``op=None`` for a named record that needed none."""

    kind: RecordKind
    key: str
    op: ChangeOp | None
    diff: tuple[FieldChange, ...] = ()


@domain_model
@dataclass(frozen=True)
class ApplyPlan:
    writes: tuple[PlannedWrite, ...] = ()
    outcomes: tuple[EntryOutcome, ...] = ()


@domain_model
@dataclass(frozen=True)
class ConfigDeclaration:
    secrets: tuple[str, ...] = ()
    #: The forges the entries need created, committed before any entry.
    forges: tuple[PlannedForge, ...] = ()
    work_sources: tuple[WorkSourceDeclaration, ...] = ()
    repositories: tuple[RepositoryDeclaration, ...] = ()
    #: The ``projects`` section, planned by the package that owns projects.
    projects: ApplyPlan = ApplyPlan()
    scopes: tuple[RecordDeclaration, ...] = ()
    routines: tuple[RecordDeclaration, ...] = ()


@domain_model
@dataclass(frozen=True)
class StoredConfig:
    """What the edge loaded for the names the document mentions — retired records included."""

    #: Stored work sources and repositories by their current slug.
    work_sources: Mapping[str, ConfiguredWorkSource]
    repositories: Mapping[str, ConfiguredRepository]
    #: The lifecycle state of each listed secret that exists; an absent name is unknown.
    secrets: Mapping[str, RecordState]
    #: For each declared repository slug no repository holds, the repository that left it.
    former_repositories: Mapping[str, ConfiguredRepository] = field(default_factory=dict)
    #: For each declared work-source slug no work source holds, the work source that left it.
    former_work_sources: Mapping[str, ConfiguredWorkSource] = field(default_factory=dict)
    #: Stored scopes and routines, by their entries' ``key``.
    scopes: Mapping[tuple[str, str], DeclarableRecord] = field(default_factory=dict)
    routines: Mapping[tuple[str, str], DeclarableRecord] = field(default_factory=dict)
    references: DeclaredReferences = DeclaredReferences()


class IConfigApplyWriter(Protocol):
    """Commits every planned write in one transaction."""

    def apply(self, writes: Sequence[PlannedWrite], *, dry_run: bool) -> None:
        """Run each write through the same in-transaction checks its per-record verb runs, in order.
        With ``dry_run`` the transaction is rolled back after the last write. The first refusal aborts
        the apply as :class:`ApplyEntryRefused`, leaving the store unchanged."""
        ...


def reconcile(declaration: ConfigDeclaration, stored: StoredConfig, ctx: ChangeContext, *, at: datetime) -> ApplyPlan:
    """The ordered writes and outcome rows that bring the stored records to the declaration.
    Raises :class:`ApplyEntryRefused` at the first entry the declaration cannot be applied for."""
    for index, name in enumerate(declaration.secrets):
        state = stored.secrets.get(name)
        if state is not RecordState.ACTIVE:
            raise ApplyEntryRefused(SECRETS_SECTION, index, SecretNotActive(name, retired=state is not None))
    plan = _Accumulator()
    for forge in declaration.forges:
        plan.writes.append(PlannedWrite(forge.section, forge.index, forge.record, None, forge.change))
        plan.outcomes.append(EntryOutcome(RecordKind.FORGE, forge.record.slug, forge.change.op, forge.change.diff))
    _reconcile_section(_WorkSourceSection(declaration.work_sources, stored, ctx, at), stored.work_sources, plan)
    _reconcile_section(_RepositorySection(declaration.repositories, stored, ctx, at), stored.repositories, plan)
    plan.writes.extend(declaration.projects.writes)
    plan.outcomes.extend(declaration.projects.outcomes)
    # Scopes reconcile first, so a routine may name a scope the same document declares.
    for section, kind, declared, stored_records in (
        (SCOPES_SECTION, RecordKind.SCOPE, declaration.scopes, stored.scopes),
        (ROUTINES_SECTION, RecordKind.ROUTINE, declaration.routines, stored.routines),
    ):
        _reconcile_section(_DeclaredRecordSection(section, kind, declared, stored, ctx, at), stored_records, plan)
    return ApplyPlan(writes=tuple(plan.writes), outcomes=tuple(plan.outcomes))


class _Accumulator:
    """The writes and outcome rows a reconcile has planned so far, in order."""

    def __init__(self) -> None:
        self.writes: list[PlannedWrite] = []
        self.outcomes: list[EntryOutcome] = []


class _Section[K: Hashable, D, R: WrittenRecord](Protocol):
    """One kind's part of the reconcile loop: the entries the document declares for it and the verbs that decide
    each. Every verb returns the record to write with its change, or ``None`` when it changes nothing."""

    section: str
    kind: RecordKind
    #: The document field a repeated entry is reported against.
    handle_field: str

    @property
    def declared(self) -> Sequence[D]: ...

    def key(self, entry: D) -> K: ...

    def label(self, entry: D) -> str:
        """What names the entry in a refusal and in a no-change outcome row."""
        ...

    def handle(self, record: R) -> str:
        """What names the record in its outcome row."""
        ...

    def guard(self, entry: D, record: R | None) -> None:
        """:class:`ConfigFieldError` when the entry cannot be applied to ``record`` (``None`` when it creates)."""
        ...

    def create(self, entry: D) -> tuple[R, ConfigChange]: ...

    def enable(self, record: R) -> tuple[R, ConfigChange] | None:
        """Re-enable a retired ``record``; ``None`` when it is not retired."""
        ...

    def edit(self, entry: D, record: R) -> tuple[R, ConfigChange] | None: ...

    def settle(self, entry: D, record: R) -> tuple[R, ConfigChange] | None:
        """Any write that settles ``record`` after its create or edit."""
        ...


class _SlugRecord(Protocol):
    """A configured record handled by its slug."""

    @property
    def slug(self) -> str: ...

    @property
    def revision(self) -> int: ...

    @property
    def retired(self) -> bool: ...

    def set_retired(
        self, retired: bool, ctx: ChangeContext, *, if_match: int | None, at: datetime
    ) -> tuple[Self, ConfigChange] | None: ...


class _SlugEntry(Protocol):
    @property
    def slug(self) -> str: ...


class _SlugSection[D: _SlugEntry, R: _SlugRecord]:
    """What a kind keyed by slug and declared by this package shares: the entry's slug is its key, its label and
    its record's handle, a retired record is re-enabled, and nothing settles after a create or edit."""

    handle_field = "slug"

    def __init__(self, declared: Sequence[D], stored: StoredConfig, ctx: ChangeContext, at: datetime) -> None:
        self.declared = declared
        self._stored = stored
        self._ctx = ctx
        self._at = at

    def key(self, entry: D) -> str:
        return entry.slug

    def label(self, entry: D) -> str:
        return entry.slug

    def handle(self, record: R) -> str:
        return record.slug

    def enable(self, record: R) -> tuple[R, ConfigChange] | None:
        return record.set_retired(False, self._ctx, if_match=None, at=self._at) if record.retired else None

    def settle(self, entry: D, record: R) -> tuple[R, ConfigChange] | None:
        return None


class _WorkSourceSection(_SlugSection[WorkSourceDeclaration, ConfiguredWorkSource]):
    section = WORK_SOURCES_SECTION
    kind = RecordKind.WORK_SOURCE

    def guard(self, entry: WorkSourceDeclaration, record: ConfiguredWorkSource | None) -> None:
        _require_unclaimed("work source", entry.slug, record, self._stored.former_work_sources.get(entry.slug))

    def create(self, entry: WorkSourceDeclaration) -> tuple[ConfiguredWorkSource, ConfigChange]:
        return ConfiguredWorkSource.new(
            entry.slug, entry.name, entry.fields, self._ctx, at=self._at, work_source_id=entry.work_source_id
        )

    def edit(
        self, entry: WorkSourceDeclaration, record: ConfiguredWorkSource
    ) -> tuple[ConfiguredWorkSource, ConfigChange] | None:
        return record.edit(entry.edit, self._ctx, if_match=None, at=self._at)


class _RepositorySection(_SlugSection[RepositoryDeclaration, ConfiguredRepository]):
    section = REPOSITORIES_SECTION
    kind = RecordKind.REPOSITORY

    def guard(self, entry: RepositoryDeclaration, record: ConfiguredRepository | None) -> None:
        _require_unclaimed("repository", entry.slug, record, self._stored.former_repositories.get(entry.slug))

    def create(self, entry: RepositoryDeclaration) -> tuple[ConfiguredRepository, ConfigChange]:
        return ConfiguredRepository.new(
            entry.slug, entry.name, entry.fields, self._ctx, at=self._at, repository_id=entry.repository_id
        )

    def edit(
        self, entry: RepositoryDeclaration, record: ConfiguredRepository
    ) -> tuple[ConfiguredRepository, ConfigChange] | None:
        return record.edit(entry.edit, self._ctx, if_match=None, at=self._at)


class _DeclaredRecordSection:
    """A kind owned outside this package — the garden's scopes and routines — whose entries bring their own
    reference checks, create and settle."""

    handle_field = "name"

    def __init__(
        self,
        section: str,
        kind: RecordKind,
        declared: Sequence[RecordDeclaration],
        stored: StoredConfig,
        ctx: ChangeContext,
        at: datetime,
    ) -> None:
        self.section = section
        self.kind = kind
        self.declared = declared
        self._stored = stored
        self._ctx = ctx
        self._at = at

    def key(self, entry: RecordDeclaration) -> tuple[str, str]:
        return entry.key

    def label(self, entry: RecordDeclaration) -> str:
        return entry.name

    def handle(self, record: DeclarableRecord) -> str:
        return record.name

    def guard(self, entry: RecordDeclaration, record: DeclarableRecord | None) -> None:
        entry.check(self._stored.references, record)

    def create(self, entry: RecordDeclaration) -> tuple[DeclarableRecord, ConfigChange]:
        return entry.create(self._ctx, at=self._at)

    def enable(self, record: DeclarableRecord) -> tuple[DeclarableRecord, ConfigChange] | None:
        return record.set_retired(False, self._ctx, if_match=None, at=self._at) if record.retired else None

    def edit(self, entry: RecordDeclaration, record: DeclarableRecord) -> tuple[DeclarableRecord, ConfigChange] | None:
        return entry.edit_record(record, self._ctx, at=self._at)

    def settle(
        self, entry: RecordDeclaration, record: DeclarableRecord
    ) -> tuple[DeclarableRecord, ConfigChange] | None:
        return entry.settle(record, self._stored.references, self._ctx, at=self._at)


def _reconcile_section[K: Hashable, D, R: WrittenRecord](
    kind: _Section[K, D, R], stored: Mapping[K, R], plan: _Accumulator
) -> None:
    seen: set[K] = set()
    for index, entry in enumerate(kind.declared):
        try:
            key = kind.key(entry)
            if key in seen:
                raise ConfigFieldError(kind.handle_field, f"{kind.label(entry)!r} is named twice in this document")
            seen.add(key)
            record = stored.get(key)
            kind.guard(entry, record)
            if record is None:
                record, change = kind.create(entry)
                _write(kind, index, record, None, change, plan)
                _settle(kind, index, entry, record, plan)
                continue
            written = False
            enabled = kind.enable(record)
            if enabled is not None:
                record, change = enabled
                _write(kind, index, record, change.revision - 1, change, plan)
                written = True
            edited = kind.edit(entry, record)
            if edited is not None:
                after, change = edited
                _write(kind, index, after, record.revision, change, plan)
                record = after
                written = True
            written = _settle(kind, index, entry, record, plan) or written
            if not written:
                plan.outcomes.append(EntryOutcome(kind.kind, kind.label(entry), None))
        except (ConfigFieldError, BuiltInWorkSource) as exc:
            raise ApplyEntryRefused(kind.section, index, exc) from exc


def _require_unclaimed(
    kind: str,
    slug: str,
    record: ConfiguredRepository | ConfiguredWorkSource | None,
    left: ConfiguredRepository | ConfiguredWorkSource | None,
) -> None:
    """:class:`ConfigFieldError` when an entry names a ``slug`` no record of its ``kind`` holds but ``left`` left:
    name that record by its current slug, or choose another."""
    if record is None and left is not None:
        left_id = left.repository_id if isinstance(left, ConfiguredRepository) else left.work_source_id
        raise ConfigFieldError(
            "slug",
            f"{slug!r} is a slug {kind} {left.slug!r} ({left_id}) left; "
            f"name that {kind} by its current slug, or choose another",
        )


def _settle[K: Hashable, D, R: WrittenRecord](
    kind: _Section[K, D, R], index: int, entry: D, record: R, plan: _Accumulator
) -> bool:
    """Plan the write that settles a record after its create or edit; ``False`` when it needs none."""
    decided = kind.settle(entry, record)
    if decided is None:
        return False
    settled, change = decided
    _write(kind, index, settled, change.revision - 1, change, plan)
    return True


def _write[K: Hashable, D, R: WrittenRecord](
    kind: _Section[K, D, R], index: int, record: R, from_revision: int | None, change: ConfigChange, plan: _Accumulator
) -> None:
    plan.writes.append(PlannedWrite(kind.section, index, record, from_revision, change))
    plan.outcomes.append(EntryOutcome(kind.kind, kind.handle(record), change.op, change.diff))
