"""Resolving a project reference — an id or a slug — to the project it names, within one tenant.

Every boundary that accepts a project resolves it here, and nothing past the boundary holds a slug
(``blizzard-product:/plans/projects/hub/spec/surfaces.md`` §Resolving a project). The rules are the shared id-or-slug
resolution's (:mod:`blizzard.hub.domain.kernel.id_or_slug`); a display name never resolves."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.ids import IdPrefix
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.kernel import id_or_slug
from blizzard.hub.domain.kernel.id_or_slug import IIdOrSlugLookup, MatchedBy, SluggedKind
from blizzard.hub.domain.projects.model import Project, ProjectId, ProjectSlug

#: Projects as the shared resolution sees them.
PROJECT_KIND = SluggedKind(IdPrefix.PROJECT, ProjectId, ProjectSlug.of)

#: The reads a resolution needs; :class:`~blizzard.hub.domain.projects.model.IReadProjectRepository` is one.
type IProjectLookup = IIdOrSlugLookup[ProjectId, ProjectSlug, Project]


@domain_model
@dataclass(frozen=True)
class ProjectMatch:
    """The project a reference names, and whether it named it by id, current slug, or a slug it left."""

    project: Project
    by: MatchedBy


def locate(value: str, projects: IProjectLookup) -> ProjectMatch | None:
    """The project ``value`` names, and how (:func:`~blizzard.hub.domain.kernel.id_or_slug.locate`)."""
    match = id_or_slug.locate(value, PROJECT_KIND, projects)
    return None if match is None else ProjectMatch(match.record, match.by)


def resolve(value: str, projects: IProjectLookup) -> Project | None:
    """The project ``value`` names (:func:`locate`), or ``None`` when it names none."""
    return id_or_slug.resolve(value, PROJECT_KIND, projects)
