"""The project label fields a view of a project-owned record carries."""

from __future__ import annotations

from typing import TypedDict

from blizzard.hub.domain.projects.model import Project


class ProjectLabelFields(TypedDict):
    """The project label fields every chunk-describing view carries."""

    project_id: str | None
    project_slug: str | None
    project_name: str | None


def project_label_fields(project_id: str | None, project: Project | None) -> ProjectLabelFields:
    """A chunk's project labels: its ``project_id``, with the current slug and name of ``project``,
    the record that id resolves to — ``None`` for each label the hub has no record for."""
    return {
        "project_id": project_id,
        "project_slug": project.slug if project is not None else None,
        "project_name": project.name if project is not None else None,
    }
