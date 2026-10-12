"""``ProjectAuthoring`` — the one writer of projects and their links.

Every verb takes the records the edge resolved (``bzh:domain-takes-objects``), asks the record's model for the change,
and hands it to the write repository with the record write. A write that changes nothing writes nothing."""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.config.changes import ChangeContext
from blizzard.hub.domain.config.repositories import ConfiguredRepository
from blizzard.hub.domain.config.work_sources import ConfiguredWorkSource
from blizzard.hub.domain.projects.links import IWriteProjectLinks, RepositoryLink, SourceLink
from blizzard.hub.domain.projects.model import IWriteProjectRepository, Project, ProjectEdit


class ProjectAuthoring:
    def __init__(self, *, projects: IWriteProjectRepository, links: IWriteProjectLinks, clock: IClock) -> None:
        self._projects = projects
        self._links = links
        self._clock = clock

    # --- Projects ----------------------------------------------------------------

    def create(self, slug: str, name: str, ctx: ChangeContext, *, description: str = "") -> Project:
        record, change = Project.new(slug, name, ctx, at=self._clock.now(), description=description)
        return self._projects.create(record, change=change)

    def edit(self, record: Project, edit: ProjectEdit, ctx: ChangeContext, *, if_match: int | None = None) -> Project:
        """A slug change moves the slug the project leaves into its former slugs, in the same write."""
        decided = record.edit(edit, ctx, if_match=if_match, at=self._clock.now())
        if decided is None:
            return record
        edited, change = decided
        return self._projects.update(
            edited, from_revision=record.revision, left_slug=record.slug_left_for(edited), change=change
        )

    # --- Source links ------------------------------------------------------------

    def link_source(
        self, project: Project, source: ConfiguredWorkSource, ctx: ChangeContext, *, if_match: int | None = None
    ) -> SourceLink:
        """Link ``project`` to ``source`` — a first link, or a relink of one it unlinked; a standing link is left as
        it is. A retired source may be linked, and a linked source may be retired."""
        now = self._clock.now()
        stored = self._links.source_link(project.project_id, source.name)
        if stored is None:
            link, change = SourceLink.new(project, source.name, ctx, at=now)
            return self._links.create_source_link(link, change=change)
        decided = stored.relink(ctx, if_match=if_match, at=now)
        if decided is None:
            return stored
        moved, change = decided
        return self._links.record_source_link(moved, from_revision=stored.revision, change=change)

    def unlink_source(self, link: SourceLink, ctx: ChangeContext, *, if_match: int | None = None) -> SourceLink:
        decided = link.unlink(ctx, if_match=if_match, at=self._clock.now())
        if decided is None:
            return link
        moved, change = decided
        return self._links.record_source_link(moved, from_revision=link.revision, change=change)

    # --- Repository links --------------------------------------------------------

    def link_repository(
        self, project: Project, repository: ConfiguredRepository, ctx: ChangeContext, *, if_match: int | None = None
    ) -> RepositoryLink:
        """Link ``project`` to ``repository`` — a first link, or a relink of one it unlinked; a standing link is left
        as it is. A retired repository may be linked, and a linked repository may be retired."""
        now = self._clock.now()
        stored = self._links.repository_link(project.project_id, repository.repository_id)
        if stored is None:
            link, change = RepositoryLink.new(project, repository.repository_id, ctx, at=now)
            return self._links.create_repository_link(link, change=change)
        decided = stored.relink(ctx, if_match=if_match, at=now)
        if decided is None:
            return stored
        moved, change = decided
        return self._links.record_repository_link(moved, from_revision=stored.revision, change=change)

    def unlink_repository(
        self, link: RepositoryLink, ctx: ChangeContext, *, if_match: int | None = None
    ) -> RepositoryLink:
        decided = link.unlink(ctx, if_match=if_match, at=self._clock.now())
        if decided is None:
            return link
        moved, change = decided
        return self._links.record_repository_link(moved, from_revision=link.revision, change=change)
