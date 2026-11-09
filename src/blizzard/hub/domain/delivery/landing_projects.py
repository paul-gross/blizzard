"""Loading the projects a chunk lands for: orchestration's choice of what to read, then :class:`LandingProject`
built from the loaded values."""

from __future__ import annotations

from collections.abc import Iterable
from types import MappingProxyType

from blizzard.hub.domain.actions.visit import LandingProject
from blizzard.hub.domain.projects.links import IReadProjectLinks
from blizzard.hub.domain.projects.model import IReadProjectRepository, ProjectId


def read_landing_project(
    project_id: ProjectId | None, *, projects: IReadProjectRepository, links: IReadProjectLinks
) -> LandingProject:
    """The chunk's project and the repository records it links, read now."""
    return read_landing_projects([project_id], projects=projects, links=links)[project_id]


def read_landing_projects(
    project_ids: Iterable[ProjectId | None], *, projects: IReadProjectRepository, links: IReadProjectLinks
) -> dict[ProjectId | None, LandingProject]:
    """:func:`read_landing_project` for each distinct id of ``project_ids``, in a bounded number of reads
    (``bzh:bulk-reconstitution``); ``None`` is the empty project."""
    ids = sorted({project_id for project_id in project_ids if project_id is not None})
    found = projects.get_many(ids) if ids else {}
    standing = links.repositories_of_projects(ids) if ids else {}
    result: dict[ProjectId | None, LandingProject] = {None: LandingProject()}
    for project_id in ids:
        project = found.get(project_id)
        positions: dict[str, float | None] = {
            link.repository_id: link.position for link in standing.get(project_id, [])
        }
        result[project_id] = LandingProject(
            project_id=project_id,
            slug=project.slug if project is not None else project_id,
            linked=frozenset(positions),
            positions=MappingProxyType(positions),
        )
    return result
