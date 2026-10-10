"""The project labels a garden view carries, read once per response.

A garden record holds its project's id; its view also names the project's current slug and display
name. A response labels every record it renders from one batched project read, never one read per
row."""

from __future__ import annotations

from collections.abc import Iterable

from blizzard.hub.api.chunk_views import ProjectLabelFields, project_label_fields
from blizzard.hub.composition import TenantServices
from blizzard.hub.domain.projects.model import Project, ProjectId


class ProjectLabels:
    """The projects a response's records belong to, keyed by id."""

    def __init__(self, projects: dict[ProjectId, Project]) -> None:
        self._projects = projects

    @classmethod
    def read(cls, services: TenantServices, project_ids: Iterable[str | None]) -> ProjectLabels:
        """One batched read of every project ``project_ids`` names."""
        ids = sorted({ProjectId(project_id) for project_id in project_ids if project_id is not None})
        return cls(services.projects.get_many(ids) if ids else {})

    @classmethod
    def of_project(cls, project: Project) -> ProjectLabels:
        """The labels of a response whose records all belong to ``project``, already resolved."""
        return cls({project.project_id: project})

    def fields(self, project_id: str | None) -> ProjectLabelFields:
        """The label fields of a record of ``project_id``."""
        if project_id is None:
            return project_label_fields(None, None)
        return project_label_fields(project_id, self._projects.get(ProjectId(project_id)))
