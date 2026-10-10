"""The project a call acts in.

A call that needs a project — a create, or a lookup by a scope slug or a routine name — and names
none acts in the tenant's project when the tenant holds exactly one. Otherwise it is refused
``422 project_required``, its body listing the tenant's projects so the caller can name one. A
listing that names no project needs none: it reads the whole tenant."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request, status
from fastapi.responses import JSONResponse

from blizzard.hub.api.deps import Services
from blizzard.hub.composition import TenantServices
from blizzard.hub.domain.projects.model import Project, ProjectRequired, sole_project

PROJECT_REQUIRED = "project_required"


def project_of_call(services: TenantServices) -> Project:
    """The project a call naming none acts in — the tenant's only one; :class:`ProjectRequired` otherwise. For an
    edge that needs a project only for part of what it was sent."""
    return sole_project(services.projects.list_all())


def call_project(services: Services) -> Project:
    """The project of a call that needs one (:func:`project_of_call`), as a dependency."""
    return project_of_call(services)


CallProject = Annotated[Project, Depends(call_project)]


def refuse_project_required(request: Request, exc: Exception) -> JSONResponse:
    """Answer a call that needs a project and named none while its tenant does not hold exactly one: ``422`` with
    the code ``project_required`` and the tenant's projects."""
    assert isinstance(exc, ProjectRequired)
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={
            "detail": {
                "code": PROJECT_REQUIRED,
                "message": str(exc),
                "projects": [
                    {"project_id": project.project_id, "slug": project.slug, "name": project.name}
                    for project in exc.projects
                ],
            }
        },
    )
