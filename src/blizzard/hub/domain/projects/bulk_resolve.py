"""Resolving many project references at once — one read of the tenant's projects, not one per reference.

Each reference resolves by :func:`~blizzard.hub.domain.projects.resolve.locate`'s rules; the tenant's projects are read
once per call and answer ids and current slugs from memory, so only a reference nothing current answers costs a
former-slug lookup (``bzh:bulk-reconstitution``)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from blizzard.hub.domain.projects.model import Project, ProjectId, ProjectSlug
from blizzard.hub.domain.projects.resolve import resolve


class IBulkProjectLookup(Protocol):
    """The reads a bulk resolution needs; :class:`~blizzard.hub.domain.projects.model.IReadProjectRepository` is one."""

    def list_all(self) -> list[Project]: ...

    def get_by_former_slug(self, slug: ProjectSlug) -> Project | None: ...


class _HeldProjects:
    """The tenant's projects as one read returned them, answering a former slug from ``fallback``."""

    def __init__(self, held: Sequence[Project], fallback: IBulkProjectLookup) -> None:
        self._by_id = {project.project_id: project for project in held}
        self._by_slug = {project.slug: project for project in held}
        self._fallback = fallback

    def get(self, project_id: ProjectId) -> Project | None:
        return self._by_id.get(project_id)

    def get_by_slug(self, slug: ProjectSlug) -> Project | None:
        return self._by_slug.get(slug.value)

    def get_by_former_slug(self, slug: ProjectSlug) -> Project | None:
        return self._fallback.get_by_former_slug(slug)


class BulkProjectResolver:
    """Resolve references to project ids within one tenant, reading its projects afresh on every call."""

    def __init__(self, projects: IBulkProjectLookup) -> None:
        self._projects = projects

    def resolve_ids(self, references: Sequence[str]) -> dict[str, ProjectId | None]:
        """Each distinct reference with the id of the project it names, ``None`` when none does. No reference, no
        read."""
        if not references:
            return {}
        held = _HeldProjects(self._projects.list_all(), self._projects)
        resolved: dict[str, ProjectId | None] = {}
        for reference in references:
            if reference not in resolved:
                project = resolve(reference, held)
                resolved[reference] = None if project is None else project.project_id
        return resolved

    def only_id(self) -> ProjectId | None:
        """The id of the tenant's project when it holds exactly one, else ``None``."""
        held = self._projects.list_all()
        return held[0].project_id if len(held) == 1 else None
