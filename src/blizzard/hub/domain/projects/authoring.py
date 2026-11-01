"""``ProjectAuthoring`` — the one writer of projects and their links.

Every verb takes the records the edge resolved (``bzh:domain-takes-objects``), asks the record's model for the change,
and hands it to the write repository with the record write. A write that changes nothing writes nothing."""

from __future__ import annotations

from collections.abc import Sequence

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.config.changes import ChangeContext
from blizzard.hub.domain.config.repositories import ConfiguredRepository
from blizzard.hub.domain.config.work_sources import ConfiguredWorkSource
from blizzard.hub.domain.projects.color import ColorPick, decide_color
from blizzard.hub.domain.projects.links import IWriteProjectLinks, RepositoryLink, SourceLink
from blizzard.hub.domain.projects.model import IWriteProjectRepository, Project, ProjectEdit
from blizzard.hub.domain.projects.order import IWriteProjectPositions, PositionFact, RankedLink, move, whole_order


class ProjectAuthoring:
    def __init__(
        self,
        *,
        projects: IWriteProjectRepository,
        links: IWriteProjectLinks,
        positions: IWriteProjectPositions,
        pick: ColorPick,
        clock: IClock,
    ) -> None:
        self._projects = projects
        self._pick = pick
        self._links = links
        self._positions = positions
        self._clock = clock

    # --- Projects ----------------------------------------------------------------

    def create(
        self, slug: str, name: str | None, ctx: ChangeContext, *, description: str = "", color: str | None = None
    ) -> Project:
        """With no ``name`` stated, the project is named by its slug; with no ``color``, it takes the tenant's
        least-used one."""
        decided = decide_color(color, lambda: (project.color for project in self._projects.list_all()), self._pick)
        record, change = Project.new(slug, name, decided, ctx, at=self._clock.now(), description=description)
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
        stored = self._links.source_link(project.project_id, source.work_source_id)
        if stored is None:
            link, change = SourceLink.new(project, source.work_source_id, ctx, at=now)
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

    # --- Landing order -----------------------------------------------------------

    def order_repositories(
        self, project: Project, repositories: Sequence[ConfiguredRepository], ctx: ChangeContext
    ) -> list[RankedLink]:
        """Rank ``repositories`` ``1.0``, ``2.0``, … in the order given and every other repository ``project`` links
        unranked; answers the project's links in their new order. ``repository_not_linked`` for a repository the
        project does not link."""
        links = self._positions.landing_links(project.project_id)
        ordered = [repository.repository_id for repository in repositories]
        return self._record(project, links, whole_order(links, ordered, project=project.slug), ctx)

    def position_repository(
        self,
        project: Project,
        repository: ConfiguredRepository,
        ctx: ChangeContext,
        *,
        after: ConfiguredRepository | None = None,
        unranked: bool = False,
    ) -> list[RankedLink]:
        """Move ``repository`` to unranked, or to just after the ranked ``after`` — to the front when ``after`` is
        ``None`` — renumbering the ranked repositories first when no midpoint is left; answers the project's links in
        their new order. ``repository_not_linked`` for a repository the project does not link."""
        links = self._positions.landing_links(project.project_id)
        facts = move(
            links,
            repository.repository_id,
            after=None if after is None else after.repository_id,
            unranked=unranked,
            project=project.slug,
        )
        return self._record(project, links, facts, ctx)

    def _record(
        self, project: Project, links: list[RankedLink], facts: Sequence[PositionFact], ctx: ChangeContext
    ) -> list[RankedLink]:
        if not facts:
            return links
        self._positions.record_positions(project.project_id, facts, at=self._clock.now(), by=ctx.actor)
        return self._positions.landing_links(project.project_id)
