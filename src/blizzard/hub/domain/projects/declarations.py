"""Projects in a configuration document, and the project a document entry or a legacy import acts in
(``bzh:config-apply``).

The apply knows no project, so its ``projects`` section is planned here and handed to it already planned. Each entry
declares a project by slug — its name and description, and the sources and repositories it links — and reconciles
before the scopes and routines, so they may name a project the same document declares. A ``sources`` or
``repositories`` list an entry states is the project's whole set: a name it leaves out is unlinked, which retires that
link; a list the entry omits leaves the stored links. A slug some project left is refused rather than taken, naming the
project that left it."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import TypeVar

from blizzard.foundation.roles import collaborator, domain_model
from blizzard.hub.domain.config.apply import PROJECTS_SECTION, ApplyEntryRefused, ApplyPlan, EntryOutcome, PlannedWrite
from blizzard.hub.domain.config.carry_over import LegacyImportRefused
from blizzard.hub.domain.config.changes import ChangeContext, ConfigChange, RecordKind
from blizzard.hub.domain.config.work_sources import BuiltInWorkSource, ConfigFieldError, require_configurable
from blizzard.hub.domain.kernel.unset import UNSET
from blizzard.hub.domain.projects.links import RepositoryLink, SourceLink
from blizzard.hub.domain.projects.model import (
    IReadProjectRepository,
    Project,
    ProjectEdit,
    ProjectId,
    ProjectRequired,
    ProjectSlug,
    sole_project,
)
from blizzard.hub.domain.projects.resolve import locate

SOURCES_FIELD = "sources"
REPOSITORIES_FIELD = "repositories"
PROJECT_FIELD = "project"

_Link = TypeVar("_Link", SourceLink, RepositoryLink)


class EntryProjectRequired(ConfigFieldError):
    """A scope or routine entry names no project, and the tenant — as the apply leaves it — does not hold exactly
    one; lists the projects it holds."""

    def __init__(self, required: ProjectRequired) -> None:
        super().__init__(PROJECT_FIELD, str(required))
        self.projects = required.projects


@domain_model
@dataclass(frozen=True)
class ProjectDeclaration:
    """A project the document names by slug: the sparse edit of the name and description it states, and the source
    and repository names it links (``None`` leaves the stored links)."""

    slug: str
    edit: ProjectEdit
    sources: tuple[str, ...] | None = None
    repositories: tuple[str, ...] | None = None


@domain_model
@dataclass(frozen=True)
class StoredProjects:
    """What the edge loaded: every project of the tenant; the project that left each slug the document names that no
    project holds; every stored link of a declared project, unlinked ones included, by ``(project id, name)``; and the
    work source and repository names stored or declared in the document."""

    projects: tuple[Project, ...]
    former: Mapping[str, Project] = field(default_factory=dict)
    source_links: Mapping[tuple[str, str], SourceLink] = field(default_factory=dict)
    repository_links: Mapping[tuple[str, str], RepositoryLink] = field(default_factory=dict)
    sources: frozenset[str] = frozenset()
    repositories: frozenset[str] = frozenset()


@domain_model
@dataclass(frozen=True)
class ProjectsPlan:
    """The planned ``projects`` section, and the tenant's projects as the apply leaves them."""

    section: ApplyPlan
    projects: tuple[Project, ...]
    former: Mapping[str, Project] = field(default_factory=dict)

    def project_of(self, section: str, index: int, reference: str | None) -> Project:
        """The project a scope or routine entry acts in: the one ``reference`` names — by id, by slug, or by a slug it
        left — or, naming none, the tenant's only project as the apply leaves it. :class:`ApplyEntryRefused` at the
        entry when the reference names no project, or none is named and the tenant does not hold exactly one."""
        try:
            if reference is None:
                try:
                    return sole_project(self.projects)
                except ProjectRequired as exc:
                    raise EntryProjectRequired(exc) from exc
            match = locate(reference, _PlannedLookup(self))
            if match is None:
                raise ConfigFieldError(PROJECT_FIELD, f"project {reference!r} is neither stored nor declared")
            return match.project
        except ConfigFieldError as exc:
            raise ApplyEntryRefused(section, index, exc) from exc


@collaborator
@dataclass(frozen=True)
class _PlannedLookup:
    """Resolves a reference against the projects as the apply leaves them."""

    plan: ProjectsPlan

    def get(self, project_id: ProjectId) -> Project | None:
        return next((p for p in self.plan.projects if p.project_id == project_id), None)

    def get_by_slug(self, slug: ProjectSlug) -> Project | None:
        return next((p for p in self.plan.projects if p.slug == slug.value), None)

    def get_by_former_slug(self, slug: ProjectSlug) -> Project | None:
        return self.plan.former.get(slug.value)


def plan_projects(
    declared: Sequence[ProjectDeclaration], stored: StoredProjects, ctx: ChangeContext, *, at: datetime
) -> ProjectsPlan:
    """The writes and outcome rows that bring the stored projects and their links to ``declared``, and the projects
    the tenant then holds. :class:`ApplyEntryRefused` at the first entry that cannot be applied."""
    current = {project.slug: project for project in stored.projects}
    writes: list[PlannedWrite] = []
    outcomes: list[EntryOutcome] = []
    seen: set[str] = set()
    for index, entry in enumerate(declared):
        try:
            if entry.slug in seen:
                raise ConfigFieldError("slug", f"{entry.slug!r} is named twice in this document")
            seen.add(entry.slug)
            planned = _Entry(index, writes, outcomes)
            record = planned.project(entry, current.get(ProjectSlug.parse(entry.slug).value), stored, ctx, at)
            current[record.slug] = record
            planned.links(
                record, SOURCES_FIELD, entry.sources, stored.sources, stored.source_links, SourceLink.new, ctx, at
            )
            planned.links(
                record,
                REPOSITORIES_FIELD,
                entry.repositories,
                stored.repositories,
                stored.repository_links,
                RepositoryLink.new,
                ctx,
                at,
            )
            if not planned.written:
                outcomes.append(EntryOutcome(RecordKind.PROJECT, entry.slug, None))
        except ConfigFieldError as exc:
            raise ApplyEntryRefused(PROJECTS_SECTION, index, exc) from exc
    return ProjectsPlan(
        section=ApplyPlan(tuple(writes), tuple(outcomes)),
        projects=tuple(sorted(current.values(), key=lambda p: p.slug)),
        former=stored.former,
    )


@collaborator
@dataclass
class _Entry:
    """The writes one project entry plans, and whether it planned any."""

    index: int
    writes: list[PlannedWrite]
    outcomes: list[EntryOutcome]
    written: bool = False

    def project(
        self,
        entry: ProjectDeclaration,
        record: Project | None,
        stored: StoredProjects,
        ctx: ChangeContext,
        at: datetime,
    ) -> Project:
        """The project after the entry: created, edited in the fields it states, or as stored."""
        if record is None:
            left = stored.former.get(entry.slug)
            if left is not None:
                raise ConfigFieldError(
                    "slug",
                    f"{entry.slug!r} is a slug project {left.slug!r} ({left.project_id}) left; "
                    f"name that project by its current slug, or choose another",
                )
            if entry.edit.name is UNSET:
                raise ConfigFieldError("name", "a project this document creates needs a name")
            description = "" if entry.edit.description is UNSET else entry.edit.description
            created, change = Project.new(entry.slug, entry.edit.name, ctx, at=at, description=description)
            self._write(RecordKind.PROJECT, created.slug, created, None, change)
            return created
        decided = record.edit(entry.edit, ctx, if_match=None, at=at)
        if decided is None:
            return record
        edited, change = decided
        self._write(RecordKind.PROJECT, edited.slug, edited, record.revision, change)
        return edited

    def links(
        self,
        project: Project,
        label: str,
        names: tuple[str, ...] | None,
        known: frozenset[str],
        stored: Mapping[tuple[str, str], _Link],
        new: Callable[..., tuple[_Link, ConfigChange]],
        ctx: ChangeContext,
        at: datetime,
    ) -> None:
        """Link every name listed, and unlink every standing link the list leaves out; nothing for an unstated list.
        Every listed name is checked before any link is planned: the built-in hub source links every project already,
        and a name must be stored or declared in the document."""
        if names is None:
            return
        listed = dict.fromkeys(names)
        for name in listed:
            if label == SOURCES_FIELD:
                try:
                    require_configurable(name)
                except BuiltInWorkSource as exc:
                    raise ConfigFieldError(label, str(exc)) from exc
            if name not in known:
                raise ConfigFieldError(label, f"{name!r} is neither stored nor declared in this document")
        for name in listed:
            link = stored.get((project.project_id, name))
            if link is None:
                created, change = new(project, name, ctx, at=at)
                self._write(created.KIND, f"{project.slug}/{name}", created, None, change)
                continue
            self._move(project, link, link.relink(ctx, if_match=None, at=at))
        for (project_id, name), link in sorted(stored.items()):
            if project_id == project.project_id and name not in listed:
                self._move(project, link, link.unlink(ctx, if_match=None, at=at))

    def _move(self, project: Project, link: _Link, decided: tuple[_Link, ConfigChange] | None) -> None:
        if decided is not None:
            moved, change = decided
            self._write(moved.KIND, f"{project.slug}/{moved.target()}", moved, link.revision, change)

    def _write(
        self, kind: RecordKind, key: str, record: object, from_revision: int | None, change: ConfigChange
    ) -> None:
        self.writes.append(PlannedWrite(PROJECTS_SECTION, self.index, record, from_revision, change))
        self.outcomes.append(EntryOutcome(kind, key, change.op, change.diff))
        self.written = True


class ImportLinks:
    """Links the work sources and repositories a legacy import creates to the tenant's only project. A tenant holding
    several is refused, naming them: the import names no project."""

    def __init__(self, projects: IReadProjectRepository) -> None:
        self._projects = projects

    def links(
        self, work_sources: Sequence[str], repositories: Sequence[str], ctx: ChangeContext, *, at: datetime
    ) -> tuple[tuple[object, ConfigChange], ...]:
        try:
            project = sole_project(self._projects.list_all())
        except ProjectRequired as exc:
            slugs = ", ".join(project.slug for project in exc.projects) or "none"
            raise LegacyImportRefused(
                f"the import links what it creates to the tenant's only project, and this tenant holds {slugs}"
            ) from exc
        return (
            *(SourceLink.new(project, name, ctx, at=at) for name in work_sources),
            *(RepositoryLink.new(project, name, ctx, at=at) for name in repositories),
        )
