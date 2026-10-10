"""Resolving a project reference — an id or a slug — to the project it names, within one tenant.

Every boundary that accepts a project resolves it here, and nothing past the boundary holds a slug
(``blizzard-product:/plans/projects/hub/spec/surfaces.md`` §Resolving a project). A display name never resolves."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.projects.model import Project, ProjectId, ProjectSlug


class MatchedBy(StrEnum):
    """Which rule a reference resolved by."""

    ID = "id"
    SLUG = "slug"
    FORMER_SLUG = "former_slug"


@domain_model
@dataclass(frozen=True)
class ProjectMatch:
    """The project a reference names, and whether it named it by id, current slug, or a slug it left."""

    project: Project
    by: MatchedBy


class IProjectLookup(Protocol):
    """The reads a resolution needs; :class:`~blizzard.hub.domain.projects.model.IReadProjectRepository` is one."""

    def get(self, project_id: ProjectId) -> Project | None: ...

    def get_by_slug(self, slug: ProjectSlug) -> Project | None: ...

    def get_by_former_slug(self, slug: ProjectSlug) -> Project | None: ...


def is_project_id(value: str) -> bool:
    """Whether ``value`` has the ``proj_<ulid>`` form, which always resolves as an id."""
    parsed = Id.parse(value)
    return parsed is not None and parsed.has_prefix(IdPrefix.PROJECT)


def locate(value: str, projects: IProjectLookup) -> ProjectMatch | None:
    """The project ``value`` names, and how: a ``proj_<ulid>`` value by id whatever its slug; otherwise a current slug,
    then a former slug. A value outside the slug alphabet — a display name among them — matches nothing."""
    if is_project_id(value):
        found = projects.get(ProjectId(value))
        return None if found is None else ProjectMatch(found, MatchedBy.ID)
    slug = ProjectSlug.of(value)
    if slug is None:
        return None
    current = projects.get_by_slug(slug)
    if current is not None:
        return ProjectMatch(current, MatchedBy.SLUG)
    former = projects.get_by_former_slug(slug)
    return None if former is None else ProjectMatch(former, MatchedBy.FORMER_SLUG)


def resolve(value: str, projects: IProjectLookup) -> Project | None:
    """The project ``value`` names (:func:`locate`), or ``None`` when it names none."""
    match = locate(value, projects)
    return None if match is None else match.project
