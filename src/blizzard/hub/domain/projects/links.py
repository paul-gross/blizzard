"""A project's links to the tenant's work sources and repositories: the link records, their verbs, and the seams.

A link is a configured record of its own, keyed by the linked record's name within its project: it carries a revision
and change facts, and unlinking is its retirement. Whether a link stands derives from its newest fact
(``bzh:facts-not-status``). The built-in ``hub`` source is linked to every project without a stored link, and cannot be
linked or unlinked."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import ClassVar, Protocol, Self

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.changes import (
    FIELDED_RECORD_TRANSITIONS,
    ChangeContext,
    ChangeOp,
    ConfigChange,
    FieldChange,
    RecordKind,
    RecordState,
    Verdict,
)
from blizzard.hub.domain.config.work_sources import ConfigRevisionConflict, require_configurable
from blizzard.hub.domain.projects.model import Project, ProjectId

#: The wire name a link's change reports its standing under.
LINKED_FIELD = "linked"

#: Which link verbs are legal from which state: an unlinked link is a retired one.
_TRANSITIONS: Mapping[RecordState, Mapping[ChangeOp, Verdict]] = FIELDED_RECORD_TRANSITIONS


@domain_model
@dataclass(frozen=True, kw_only=True)
class _Link:
    """What a source link and a repository link share; ``target`` is the linked record's name."""

    KIND: ClassVar[RecordKind]
    LABEL: ClassVar[str]

    project_id: ProjectId
    revision: int
    linked: bool = True

    def target(self) -> str:
        raise NotImplementedError

    def require_revision(self, if_match: int | None) -> None:
        """:class:`ConfigRevisionConflict` when ``if_match`` names a revision other than the stored one."""
        if if_match is not None and if_match != self.revision:
            raise ConfigRevisionConflict(self.LABEL, f"{self.project_id}/{self.target()}", current=self.revision)

    def unlink(self, ctx: ChangeContext, *, if_match: int | None, at: datetime) -> tuple[Self, ConfigChange] | None:
        """Retire the link; ``None`` when it is already unlinked."""
        return self._set_linked(False, ctx, if_match=if_match, at=at)

    def relink(self, ctx: ChangeContext, *, if_match: int | None, at: datetime) -> tuple[Self, ConfigChange] | None:
        """Link again; ``None`` when it already stands."""
        return self._set_linked(True, ctx, if_match=if_match, at=at)

    def _set_linked(
        self, linked: bool, ctx: ChangeContext, *, if_match: int | None, at: datetime
    ) -> tuple[Self, ConfigChange] | None:
        self.require_revision(if_match)
        op = ChangeOp.ENABLE if linked else ChangeOp.RETIRE
        if _TRANSITIONS[RecordState.of(not self.linked)][op] is Verdict.NO_OP:
            return None
        moved = replace(self, revision=self.revision + 1, linked=linked)
        return moved, moved._change(ctx, op, (FieldChange(LINKED_FIELD, self.linked, linked),), at)

    def _change(self, ctx: ChangeContext, op: ChangeOp, changes: tuple[FieldChange, ...], at: datetime) -> ConfigChange:
        return ConfigChange.of(
            ctx, self.KIND, self.target(), self.revision, op, changes, at, project_id=self.project_id
        )


_CREATED = (FieldChange(LINKED_FIELD, None, True),)


@domain_model
@dataclass(frozen=True, kw_only=True)
class SourceLink(_Link):
    """A project draws from ``source_name``. It never narrows what the project may ingest from the source."""

    KIND: ClassVar[RecordKind] = RecordKind.PROJECT_SOURCE_LINK
    LABEL: ClassVar[str] = "source link"

    source_name: str

    def target(self) -> str:
        return self.source_name

    @classmethod
    def new(cls, project: Project, source_name: str, ctx: ChangeContext, *, at: datetime) -> tuple[Self, ConfigChange]:
        """The first link of ``project`` to ``source_name``. The built-in hub source is refused: every project is
        linked to it already."""
        require_configurable(source_name)
        link = cls(project_id=project.project_id, source_name=source_name, revision=1)
        return link, link._change(ctx, ChangeOp.CREATE, _CREATED, at)


@domain_model
@dataclass(frozen=True, kw_only=True)
class RepositoryLink(_Link):
    """A project's chunks may land in ``repository_name``; it grants nothing else."""

    KIND: ClassVar[RecordKind] = RecordKind.PROJECT_REPOSITORY_LINK
    LABEL: ClassVar[str] = "repository link"

    repository_name: str

    def target(self) -> str:
        return self.repository_name

    @classmethod
    def new(
        cls, project: Project, repository_name: str, ctx: ChangeContext, *, at: datetime
    ) -> tuple[Self, ConfigChange]:
        """The first link of ``project`` to ``repository_name``."""
        link = cls(project_id=project.project_id, repository_name=repository_name, revision=1)
        return link, link._change(ctx, ChangeOp.CREATE, _CREATED, at)


class IReadProjectLinks(Protocol):
    """Read-only link operations, within one tenant."""

    def source_link(self, project_id: ProjectId, source_name: str) -> SourceLink | None:
        """The stored link, linked or not; ``None`` when the project never linked the source."""
        ...

    def repository_link(self, project_id: ProjectId, repository_name: str) -> RepositoryLink | None:
        """The stored link, linked or not; ``None`` when the project never linked the repository."""
        ...

    def sources_of(self, project_id: ProjectId) -> list[SourceLink]:
        """The project's standing source links, ordered by source name. The built-in hub source has none."""
        ...

    def repositories_of(self, project_id: ProjectId) -> list[RepositoryLink]:
        """The project's standing repository links, ordered by repository name."""
        ...

    def projects_linking_source(self, source_name: str) -> list[ProjectId]:
        """The projects a standing link draws from ``source_name``, ordered by id."""
        ...

    def linked_at(self, project_id: ProjectId, source_name: str, at: datetime) -> bool:
        """Whether ``project_id`` drew from ``source_name`` at ``at``, by the newest link fact set by then. The
        built-in hub source is always linked."""
        ...


class IWriteProjectLinks(IReadProjectLinks, Protocol):
    """Adds the link writes. Each appends a fact of the link's standing (``set_at`` = ``change.recorded_at``,
    ``set_by`` = ``change.actor``) and commits ``change``, in the same transaction as the link write."""

    def create_source_link(self, link: SourceLink, *, change: ConfigChange) -> SourceLink: ...

    def record_source_link(self, link: SourceLink, *, from_revision: int, change: ConfigChange) -> SourceLink:
        """Compare-and-set ``from_revision`` to ``link.revision``; :class:`ConfigRevisionConflict` when it moved."""
        ...

    def create_repository_link(self, link: RepositoryLink, *, change: ConfigChange) -> RepositoryLink: ...

    def record_repository_link(
        self, link: RepositoryLink, *, from_revision: int, change: ConfigChange
    ) -> RepositoryLink:
        """Compare-and-set ``from_revision`` to ``link.revision``; :class:`ConfigRevisionConflict` when it moved."""
        ...
