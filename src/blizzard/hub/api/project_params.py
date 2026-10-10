"""The project a call acts in, and the project a call names.

A call that needs a project — a create, or a lookup by a scope slug or a routine name — and names
none acts in the tenant's project when the tenant holds exactly one. Otherwise it is refused
``422 project_required``, its body listing the tenant's projects so the caller can name one. A
listing that names no project needs none: it reads the whole tenant.

A project a call names — a path segment, ``?project=``, a body field — is its id or its slug,
resolved here to the project within the call's tenant; nothing past the edge holds a slug. A slug
the project left still resolves to it, served without a redirect; a display name never resolves.
An unresolved project answers ``404`` on a path and ``422`` in a query or a body."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from blizzard.hub.api.deps import Services
from blizzard.hub.composition import TenantServices
from blizzard.hub.domain.projects.model import Project, ProjectId, ProjectRequired, sole_project
from blizzard.wire.project import ProjectRef, ProjectRequiredBody

PROJECT_REQUIRED = "project_required"
#: The validation ``type`` of a ``422`` naming a project the tenant does not hold.
UNKNOWN_PROJECT = "unknown_project"


def project_of_call(services: TenantServices) -> Project:
    """The project a call naming none acts in — the tenant's only one; :class:`ProjectRequired` otherwise. For an
    edge that needs a project only for part of what it was sent."""
    return sole_project(services.projects.list_all())


def call_project(services: Services) -> Project:
    """The project of a call that needs one (:func:`project_of_call`), as a dependency."""
    return project_of_call(services)


CallProject = Annotated[Project, Depends(call_project)]


def _unknown(value: str) -> str:
    return f"unknown project {value!r}"


def _unprocessable(where: str, field: str, value: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=[{"loc": [where, field], "msg": _unknown(value), "type": UNKNOWN_PROJECT}],
    )


def path_project(project: str, services: Services) -> Project:
    """The project the ``{project}`` path segment names, by id, slug or former slug; ``404`` when it names none."""
    found = services.projects.resolve(project)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_unknown(project))
    return found


PathProject = Annotated[Project, Depends(path_project)]


def query_project(services: Services, project: str | None = None) -> ProjectId | None:
    """The project ``?project=`` narrows a read to — ``None`` when the query names none, so the read takes the whole
    tenant; ``422`` when it names a project the tenant does not hold."""
    if project is None:
        return None
    found = services.projects.resolve(project)
    if found is None:
        raise _unprocessable("query", "project", project)
    return found.project_id


QueryProject = Annotated[ProjectId | None, Depends(query_project)]


def body_project(value: str, services: TenantServices, *, field: str = "project") -> Project:
    """The project a body's ``field`` names, by id, slug or former slug; ``422`` naming the field when it names
    none."""
    found = services.projects.resolve(value)
    if found is None:
        raise _unprocessable("body", field, value)
    return found


def project_ref(project: Project) -> ProjectRef:
    """``project`` as a refusal names it."""
    return ProjectRef(project_id=project.project_id, slug=project.slug, name=project.name)


def refuse_project_required(request: Request, exc: Exception) -> JSONResponse:
    """Answer a call that needs a project and named none while its tenant does not hold exactly one: ``422`` with
    the code ``project_required`` and the tenant's projects."""
    assert isinstance(exc, ProjectRequired)
    body = ProjectRequiredBody(message=str(exc), projects=[project_ref(project) for project in exc.projects])
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"detail": body.model_dump()})
