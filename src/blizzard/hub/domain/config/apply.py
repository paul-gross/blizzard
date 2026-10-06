"""Declarative apply — a document reconciled against the stored records (``bzh:config-apply``).

An apply is a door and not an owner: it decides, per named record, the same creates, edits and enables the
per-kind verbs decide, and hands the whole ordered plan to :class:`IConfigApplyWriter` to commit as one
transaction. It never retires a record the document leaves out. A record the document omits a field of keeps
that field (sparse merge, ``bzh:configured-record``); a record that already stands as declared writes nothing."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.changes import (
    ChangeContext,
    ChangeOp,
    ConfigChange,
    FieldChange,
    RecordKind,
    RecordState,
)
from blizzard.hub.domain.config.repositories import (
    ConfiguredRepository,
    RepositoryEdit,
    RepositoryFields,
)
from blizzard.hub.domain.config.work_sources import (
    BuiltInWorkSource,
    ConfigFieldError,
    ConfiguredWorkSource,
    WorkSourceEdit,
    WorkSourceFields,
    require_configurable,
)

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
    """A work source the document names: its create fields, and the sparse edit of only the fields it states."""

    name: str
    fields: WorkSourceFields
    edit: WorkSourceEdit


@domain_model
@dataclass(frozen=True)
class RepositoryDeclaration:
    """A repository the document names: its create fields, and the sparse edit of only the fields it states."""

    name: str
    fields: RepositoryFields
    edit: RepositoryEdit


class DeclarableRecord(Protocol):
    """A configured record of a kind owned outside this package, reconciled by its own verbs. Each verb
    returns the record to write with its change, or ``None`` when it changes nothing."""

    @property
    def name(self) -> str: ...

    @property
    def revision(self) -> int: ...

    @property
    def retired(self) -> bool: ...

    def edit(
        self, edit: Any, ctx: ChangeContext, *, if_match: int | None, at: datetime
    ) -> tuple[DeclarableRecord, ConfigChange] | None: ...

    def set_retired(
        self, retired: bool, ctx: ChangeContext, *, if_match: int | None, at: datetime
    ) -> tuple[DeclarableRecord, ConfigChange] | None: ...


@domain_model
@dataclass(frozen=True)
class DeclaredReferences:
    """What an entry's reference checks read: every scope slug stored or declared in the document, the
    graph names that resolve to an enabled graph, and each stored routine's linked scope set by name."""

    scopes: frozenset[str] = frozenset()
    enabled_graphs: frozenset[str] = frozenset()
    linked_scopes: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


class RecordDeclaration(Protocol):
    """An entry of a kind owned outside this package: the sparse ``edit`` of the fields it states, its
    reference ``check``, the record it ``create``s, and any write that ``settle``s the record afterwards."""

    @property
    def name(self) -> str: ...

    @property
    def edit(self) -> object: ...

    def check(self, refs: DeclaredReferences, stored: DeclarableRecord | None) -> None:
        """:class:`ConfigFieldError` naming the field whose reference does not resolve."""
        ...

    def create(self, ctx: ChangeContext, *, at: datetime) -> tuple[DeclarableRecord, ConfigChange]: ...

    def settle(
        self, record: DeclarableRecord, refs: DeclaredReferences, ctx: ChangeContext, *, at: datetime
    ) -> tuple[DeclarableRecord, ConfigChange] | None: ...


@domain_model
@dataclass(frozen=True)
class ConfigDeclaration:
    secrets: tuple[str, ...] = ()
    work_sources: tuple[WorkSourceDeclaration, ...] = ()
    repositories: tuple[RepositoryDeclaration, ...] = ()
    scopes: tuple[RecordDeclaration, ...] = ()
    routines: tuple[RecordDeclaration, ...] = ()


@domain_model
@dataclass(frozen=True)
class StoredConfig:
    """What the edge loaded for the names the document mentions — retired records included."""

    work_sources: Mapping[str, ConfiguredWorkSource]
    repositories: Mapping[str, ConfiguredRepository]
    #: The lifecycle state of each listed secret that exists; an absent name is unknown.
    secrets: Mapping[str, RecordState]
    scopes: Mapping[str, DeclarableRecord] = field(default_factory=dict)
    routines: Mapping[str, DeclarableRecord] = field(default_factory=dict)
    references: DeclaredReferences = DeclaredReferences()


@domain_model
@dataclass(frozen=True)
class PlannedWrite:
    """One record write the apply will commit: the record after the write, the revision it moves from
    (``None`` for a create) and the change row committed beside it."""

    section: str
    index: int
    record: ConfiguredWorkSource | ConfiguredRepository | DeclarableRecord
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
    writes: tuple[PlannedWrite, ...]
    outcomes: tuple[EntryOutcome, ...]


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
    writes: list[PlannedWrite] = []
    outcomes: list[EntryOutcome] = []
    _reconcile_section(
        WORK_SOURCES_SECTION,
        RecordKind.WORK_SOURCE,
        declaration.work_sources,
        stored.work_sources,
        ctx,
        at,
        writes,
        outcomes,
        guard=lambda d, _: require_configurable(d.name),
        create=lambda d: ConfiguredWorkSource.new(d.name, d.fields, ctx, at=at),
    )
    _reconcile_section(
        REPOSITORIES_SECTION,
        RecordKind.REPOSITORY,
        declaration.repositories,
        stored.repositories,
        ctx,
        at,
        writes,
        outcomes,
        guard=lambda d, _: None,
        create=lambda d: ConfiguredRepository.new(d.name, d.fields, ctx, at=at),
    )
    # Scopes reconcile first, so a routine may name a scope the same document declares.
    for section, kind, declared, stored_records in (
        (SCOPES_SECTION, RecordKind.SCOPE, declaration.scopes, stored.scopes),
        (ROUTINES_SECTION, RecordKind.ROUTINE, declaration.routines, stored.routines),
    ):
        _reconcile_section(
            section,
            kind,
            declared,
            stored_records,
            ctx,
            at,
            writes,
            outcomes,
            guard=lambda d, record: d.check(stored.references, record),
            create=lambda d: d.create(ctx, at=at),
            settle=lambda d, record: d.settle(record, stored.references, ctx, at=at),
        )
    return ApplyPlan(writes=tuple(writes), outcomes=tuple(outcomes))


def _reconcile_section(
    section: str,
    kind: RecordKind,
    declared: Sequence[Any],
    stored: Mapping[str, Any],
    ctx: ChangeContext,
    at: datetime,
    writes: list[PlannedWrite],
    outcomes: list[EntryOutcome],
    *,
    guard: Callable[[Any, Any], None],
    create: Callable[[Any], tuple[Any, ConfigChange]],
    settle: Callable[[Any, Any], tuple[Any, ConfigChange] | None] = lambda d, record: None,
) -> None:
    seen: set[str] = set()
    for index, entry in enumerate(declared):
        try:
            if entry.name in seen:
                raise ConfigFieldError("name", f"{entry.name!r} is named twice in this document")
            seen.add(entry.name)
            record = stored.get(entry.name)
            guard(entry, record)
            if record is None:
                record, change = create(entry)
                _write(section, index, kind, record, None, change, writes, outcomes)
                _settle(section, index, kind, settle(entry, record), writes, outcomes)
                continue
            written = False
            if record.retired:
                decided = record.set_retired(False, ctx, if_match=None, at=at)
                if decided is not None:
                    record, change = decided
                    _write(section, index, kind, record, change.revision - 1, change, writes, outcomes)
                    written = True
            decided = record.edit(entry.edit, ctx, if_match=None, at=at)  # type: ignore[arg-type]
            if decided is not None:
                edited, change = decided
                _write(section, index, kind, edited, record.revision, change, writes, outcomes)
                record = edited
                written = True
            written = _settle(section, index, kind, settle(entry, record), writes, outcomes) or written
            if not written:
                outcomes.append(EntryOutcome(kind, entry.name, None))
        except (ConfigFieldError, BuiltInWorkSource) as exc:
            raise ApplyEntryRefused(section, index, exc) from exc


def _settle(
    section: str,
    index: int,
    kind: RecordKind,
    decided: tuple[Any, ConfigChange] | None,
    writes: list[PlannedWrite],
    outcomes: list[EntryOutcome],
) -> bool:
    """Plan the write that settles a record after its create or edit; ``False`` when it needs none."""
    if decided is None:
        return False
    settled, change = decided
    _write(section, index, kind, settled, change.revision - 1, change, writes, outcomes)
    return True


def _write(
    section: str,
    index: int,
    kind: RecordKind,
    record: ConfiguredWorkSource | ConfiguredRepository | DeclarableRecord,
    from_revision: int | None,
    change: ConfigChange,
    writes: list[PlannedWrite],
    outcomes: list[EntryOutcome],
) -> None:
    writes.append(PlannedWrite(section, index, record, from_revision, change))
    outcomes.append(EntryOutcome(kind, record.name, change.op, change.diff))
