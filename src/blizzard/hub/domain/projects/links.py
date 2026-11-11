"""A project's links to the tenant's work sources and repositories: the link records, their verbs, and the seams.

A link is a configured record of its own, keyed by the linked record's id within its project: it carries a revision
and change facts, and unlinking is its retirement. Whether a link stands derives from its newest fact
(``bzh:facts-not-status``). The built-in ``hub`` source is linked to every project without a stored link, and cannot be
linked or unlinked."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import ClassVar, Protocol, Self

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config import lifecycle
from blizzard.hub.domain.config.changes import (
    ChangeContext,
    ChangeOp,
    ConfigChange,
    FieldChange,
    RecordKind,
)
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.config.repositories import ConfiguredRepository, RepositoryId
from blizzard.hub.domain.config.work_sources import WorkSourceId, require_configurable
from blizzard.hub.domain.projects.model import Project, ProjectId

#: The wire name a link's change reports its standing under.
LINKED_FIELD = "linked"

#: The wire name of a repository link's own integration branch.
BASE_BRANCH_FIELD = "base_branch"

#: Characters no git branch name holds.
_REFUSED_BRANCH_CHARACTERS = frozenset("~^:?*[\\")


def parse_base_branch(raw: str) -> str:
    """``raw`` as a link's base branch: a valid git branch name, unpadded. Whether the branch exists on the forge is
    never checked here. :class:`ConfigFieldError` on ``base_branch`` otherwise."""
    problem = _branch_problem(raw)
    if problem is not None:
        raise ConfigFieldError(BASE_BRANCH_FIELD, f"{problem}, got {raw!r}")
    return raw


def _branch_problem(name: str) -> str | None:
    """Why ``name`` is no git branch name, or ``None`` when it is one."""
    if not name.strip():
        return "must not be blank"
    if name != name.strip():
        return "must have no leading or trailing whitespace"
    if any(c.isspace() or ord(c) < 0x20 or ord(c) == 0x7F for c in name):
        return "must hold no whitespace or control characters"
    if any(c in _REFUSED_BRANCH_CHARACTERS for c in name):
        return "must hold none of ~ ^ : ? * [ \\"
    if ".." in name or "@{" in name:
        return "must hold neither '..' nor '@{'"
    if name.startswith("-"):
        return "must not start with '-'"
    if name.startswith("/") or name.endswith("/") or "//" in name:
        return "must not start or end with '/', or hold '//'"
    if name.endswith("."):
        return "must not end with '.'"
    if any(component.startswith(".") or component.endswith(".lock") for component in name.split("/")):
        return "must hold no component starting with '.' or ending in '.lock'"
    if name == "@":
        return "must not be '@'"
    return None


@domain_model
@dataclass(frozen=True, kw_only=True)
class _Link:
    """What a source link and a repository link share; ``target`` is the linked record's id."""

    KIND: ClassVar[RecordKind]
    LABEL: ClassVar[str]

    project_id: ProjectId
    revision: int
    linked: bool = True

    def target(self) -> str:
        raise NotImplementedError

    def require_revision(self, if_match: int | None) -> None:
        """:class:`ConfigRevisionConflict` when ``if_match`` names a revision other than the stored one."""
        lifecycle.require_revision(if_match, self.revision, kind=self.LABEL, key=f"{self.project_id}/{self.target()}")

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
        op = lifecycle.transition(not self.linked, not linked)
        if op is None:
            return None
        moved = replace(self, revision=self.revision + 1, linked=linked)
        changes = lifecycle.field_changes(((LINKED_FIELD, self.linked, linked),))
        return moved, moved._change(ctx, op, changes, at)

    def _change(self, ctx: ChangeContext, op: ChangeOp, changes: tuple[FieldChange, ...], at: datetime) -> ConfigChange:
        return ConfigChange.of(
            ctx, self.KIND, self.target(), self.revision, op, changes, at, project_id=self.project_id
        )


_CREATED = (FieldChange(LINKED_FIELD, None, True),)


@domain_model
@dataclass(frozen=True, kw_only=True)
class SourceLink(_Link):
    """A project draws from ``work_source_id``. It never narrows what the project may ingest from the source."""

    KIND: ClassVar[RecordKind] = RecordKind.PROJECT_SOURCE_LINK
    LABEL: ClassVar[str] = "source link"

    work_source_id: WorkSourceId

    def target(self) -> str:
        return self.work_source_id

    @classmethod
    def new(
        cls, project: Project, work_source_id: WorkSourceId, ctx: ChangeContext, *, at: datetime
    ) -> tuple[Self, ConfigChange]:
        """The first link of ``project`` to ``work_source_id``. The built-in hub source is refused: every project is
        linked to it already."""
        require_configurable(work_source_id)
        link = cls(project_id=project.project_id, work_source_id=work_source_id, revision=1)
        return link, link._change(ctx, ChangeOp.CREATE, _CREATED, at)


@domain_model
@dataclass(frozen=True, kw_only=True)
class RepositoryLink(_Link):
    """A project's chunks may land in ``repository_id``; it grants nothing else. ``position`` is the link's place in
    the project's landing order as a read found it — ``None`` when unranked — and is no field of the link's revision.
    ``base_branch`` is the link's own integration branch — ``None`` inherits the repository's — and is a field of the
    link's revision."""

    KIND: ClassVar[RecordKind] = RecordKind.PROJECT_REPOSITORY_LINK
    LABEL: ClassVar[str] = "repository link"

    repository_id: RepositoryId
    position: float | None = None
    base_branch: str | None = None

    def target(self) -> str:
        return self.repository_id

    def effective_base_branch(self, repository: ConfiguredRepository) -> str:
        """The branch the link's chunks start from and land into: its own, else ``repository``'s."""
        return repository.fields.base_branch if self.base_branch is None else self.base_branch

    def set_base_branch(
        self, base_branch: str | None, ctx: ChangeContext, *, if_match: int | None, at: datetime
    ) -> tuple[Self, ConfigChange] | None:
        """Name the link's own branch, or clear it with ``None`` so the link inherits again; ``None`` back when the
        link already stands there. The revision check comes first, even for an edit that changes nothing.
        :class:`ConfigFieldError` on ``base_branch`` for a name that is no git branch name."""
        self.require_revision(if_match)
        decided = None if base_branch is None else parse_base_branch(base_branch)
        changes = lifecycle.field_changes(((BASE_BRANCH_FIELD, self.base_branch, decided),))
        if not changes:
            return None
        moved = replace(self, revision=self.revision + 1, base_branch=decided)
        return moved, moved._change(ctx, ChangeOp.EDIT, changes, at)

    @classmethod
    def new(
        cls, project: Project, repository_id: RepositoryId, ctx: ChangeContext, *, at: datetime
    ) -> tuple[Self, ConfigChange]:
        """The first link of ``project`` to ``repository_id``."""
        link = cls(project_id=project.project_id, repository_id=repository_id, revision=1)
        return link, link._change(ctx, ChangeOp.CREATE, _CREATED, at)


class IReadProjectLinks(Protocol):
    """Read-only link operations, within one tenant."""

    def source_link(self, project_id: ProjectId, work_source_id: WorkSourceId) -> SourceLink | None:
        """The stored link, linked or not; ``None`` when the project never linked the source."""
        ...

    def repository_link(self, project_id: ProjectId, repository_id: RepositoryId) -> RepositoryLink | None:
        """The stored link, linked or not; ``None`` when the project never linked the repository."""
        ...

    def sources_of(self, project_id: ProjectId) -> list[SourceLink]:
        """The project's standing source links, ordered by source id. The built-in hub source has none."""
        ...

    def repositories_of(self, project_id: ProjectId) -> list[RepositoryLink]:
        """The project's standing repository links, ordered by repository id."""
        ...

    def projects_linking_source(self, work_source_id: str) -> list[ProjectId]:
        """The projects a standing link draws from the configured source ``work_source_id`` — a pointer's ``source``,
        so a :data:`WorkSourceId` held as a plain key — ordered by id."""
        ...

    def source_links_for(
        self, pairs: Sequence[tuple[ProjectId, WorkSourceId]]
    ) -> dict[tuple[ProjectId, WorkSourceId], SourceLink]:
        """:meth:`source_link`'s batched sibling (``bzh:bulk-reconstitution``): each stored link of ``pairs``, linked
        or not, keyed by its pair; a pair the project never linked is dropped."""
        ...

    def repository_links_for(
        self, pairs: Sequence[tuple[ProjectId, RepositoryId]]
    ) -> dict[tuple[ProjectId, RepositoryId], RepositoryLink]:
        """:meth:`repository_link`'s batched sibling: each stored link of ``pairs``, linked or not, keyed by its pair;
        a pair the project never linked is dropped."""
        ...

    def sources_of_projects(self, project_ids: Sequence[ProjectId]) -> dict[ProjectId, list[SourceLink]]:
        """:meth:`sources_of`'s batched sibling: each project's standing source links, ordered by source id; a project
        with none is dropped."""
        ...

    def repositories_of_projects(self, project_ids: Sequence[ProjectId]) -> dict[ProjectId, list[RepositoryLink]]:
        """:meth:`repositories_of`'s batched sibling: each project's standing repository links, ordered by repository
        id; a project with none is dropped."""
        ...

    def projects_linking_sources(self, work_source_ids: Sequence[str]) -> dict[str, list[ProjectId]]:
        """:meth:`projects_linking_source`'s batched sibling: each source's linking projects, ordered by id; a source
        no project links is dropped."""
        ...

    def linked_at(self, project_id: ProjectId, source: str, at: datetime) -> bool:
        """Whether ``project_id`` drew from ``source`` — a source id, or ``hub`` — at ``at``, by the newest link fact
        set by then. The built-in hub source is always linked."""
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
