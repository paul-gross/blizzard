"""Declarative apply — a document reconciled against the stored records (``bzh:config-apply``).

An apply is a door and not an owner: it decides, per named record, the same creates, edits and enables the
per-kind verbs decide, and hands the whole ordered plan to :class:`IConfigApplyWriter` to commit as one
transaction. It never retires a record the document leaves out. A record the document omits a field of keeps
that field (sparse merge, ``bzh:configured-record``); a record that already stands as declared writes nothing."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

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


@domain_model
@dataclass(frozen=True)
class ConfigDeclaration:
    secrets: tuple[str, ...] = ()
    work_sources: tuple[WorkSourceDeclaration, ...] = ()
    repositories: tuple[RepositoryDeclaration, ...] = ()


@domain_model
@dataclass(frozen=True)
class StoredConfig:
    """What the edge loaded for the names the document mentions — retired records included."""

    work_sources: Mapping[str, ConfiguredWorkSource]
    repositories: Mapping[str, ConfiguredRepository]
    #: The lifecycle state of each listed secret that exists; an absent name is unknown.
    secrets: Mapping[str, RecordState]


@domain_model
@dataclass(frozen=True)
class PlannedWrite:
    """One record write the apply will commit: the record after the write, the revision it moves from
    (``None`` for a create) and the change row committed beside it."""

    section: str
    index: int
    record: ConfiguredWorkSource | ConfiguredRepository
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
        guard=require_configurable,
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
        guard=lambda name: None,
        create=lambda d: ConfiguredRepository.new(d.name, d.fields, ctx, at=at),
    )
    return ApplyPlan(writes=tuple(writes), outcomes=tuple(outcomes))


def _reconcile_section(
    section: str,
    kind: RecordKind,
    declared: Sequence[WorkSourceDeclaration] | Sequence[RepositoryDeclaration],
    stored: Mapping[str, ConfiguredWorkSource] | Mapping[str, ConfiguredRepository],
    ctx: ChangeContext,
    at: datetime,
    writes: list[PlannedWrite],
    outcomes: list[EntryOutcome],
    *,
    guard: Callable[[str], None],
    create: Callable[..., tuple[ConfiguredWorkSource | ConfiguredRepository, ConfigChange]],
) -> None:
    seen: set[str] = set()
    for index, entry in enumerate(declared):
        try:
            guard(entry.name)
            if entry.name in seen:
                raise ConfigFieldError("name", f"{entry.name!r} is named twice in this document")
            seen.add(entry.name)
            record = stored.get(entry.name)
            if record is None:
                created, change = create(entry)
                _write(section, index, kind, created, None, change, writes, outcomes)
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
                written = True
            if not written:
                outcomes.append(EntryOutcome(kind, entry.name, None))
        except (ConfigFieldError, BuiltInWorkSource) as exc:
            raise ApplyEntryRefused(section, index, exc) from exc


def _write(
    section: str,
    index: int,
    kind: RecordKind,
    record: ConfiguredWorkSource | ConfiguredRepository,
    from_revision: int | None,
    change: ConfigChange,
    writes: list[PlannedWrite],
    outcomes: list[EntryOutcome],
) -> None:
    writes.append(PlannedWrite(section, index, record, from_revision, change))
    outcomes.append(EntryOutcome(kind, record.name, change.op, change.diff))
