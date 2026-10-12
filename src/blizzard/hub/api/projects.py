"""Project routes — the configured-record verbs a project takes (create, list, show, patch; a project is never
retired), and a project's source and repository links (list, link, retire to unlink). Human-plane throughout
(``reject_runner_principal``). Writes go only through ``ProjectAuthoring``; the actor and door come from the request's
:class:`ChangeContext`.

``{project}`` is the project's id or slug, a slug it left among them (:func:`path_project`). The built-in ``hub``
source is linked to every project without a stored link: a listing reports it, linking it changes nothing, and
unlinking it is refused."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status

from blizzard.auth_core import Permission
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.deps import Services
from blizzard.hub.api.door import RequestDoor, change_context
from blizzard.hub.api.project_params import PathProject
from blizzard.hub.api.repository_params import PathRepository
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.composition import TenantServices
from blizzard.hub.domain.config.repositories import ConfiguredRepository
from blizzard.hub.domain.config.work_sources import (
    ConfigFieldError,
    ConfigRevisionConflict,
    ConfiguredWorkSource,
    is_built_in,
)
from blizzard.hub.domain.kernel.hub_source import RESERVED_HUB_SOURCE_NAME
from blizzard.hub.domain.kernel.unset import UNSET
from blizzard.hub.domain.projects.links import RepositoryLink, SourceLink
from blizzard.hub.domain.projects.model import Project, ProjectEdit, ProjectSlugTaken
from blizzard.wire.project import (
    ProjectCreateRequest,
    ProjectPatchRequest,
    ProjectsListView,
    ProjectView,
    RepositoryLinksView,
    RepositoryLinkView,
    SourceLinksView,
    SourceLinkView,
)

router = APIRouter(
    prefix="/api", tags=["projects"], dependencies=[Depends(reject_runner_principal), Depends(tenant_bound)]
)

_VIEW = Depends(require(Permission.FLEET_VIEW))
Editor = Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))]
IfMatch = Annotated[int | None, Header()]


def _view(record: Project) -> ProjectView:
    return ProjectView(
        project_id=record.project_id,
        slug=record.slug,
        name=record.name,
        description=record.description,
        revision=record.revision,
        created_at=iso_utc(record.created_at),
    )


def _source_view(link: SourceLink) -> SourceLinkView:
    return SourceLinkView(project_id=link.project_id, name=link.source_name, linked=link.linked, revision=link.revision)


def _built_in_view(project: Project) -> SourceLinkView:
    return SourceLinkView(project_id=project.project_id, name=RESERVED_HUB_SOURCE_NAME, linked=True, built_in=True)


def _repository_view(link: RepositoryLink, repository: ConfiguredRepository) -> RepositoryLinkView:
    return RepositoryLinkView(
        project_id=link.project_id,
        repository_id=link.repository_id,
        slug=repository.slug,
        name=repository.name,
        linked=link.linked,
        revision=link.revision,
    )


def _unprocessable(field: str, message: str, kind: str = "value_error", where: str = "body") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=[{"loc": [where, field], "msg": message, "type": kind}],
    )


def _conflict(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


def _not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _edit(request: ProjectPatchRequest) -> ProjectEdit:
    """The sparse edit ``request`` states; ``422`` naming a field it sets to ``null``."""
    present = {field: getattr(request, field) for field in request.model_fields_set}
    for field, value in present.items():
        if value is None:
            raise _unprocessable(field, "may not be null")
    return ProjectEdit(**{field: present.get(field, UNSET) for field in ("slug", "name", "description")})


# --- Projects ----------------------------------------------------------------------------------------------------


@router.get("/projects", response_model=ProjectsListView, dependencies=[_VIEW])
def list_projects(services: Services) -> ProjectsListView:
    """Every project of the tenant, by slug."""
    return ProjectsListView(projects=[_view(record) for record in services.projects.list_all()])


@router.post("/projects", response_model=ProjectView, status_code=status.HTTP_201_CREATED)
def create_project(
    request: ProjectCreateRequest, identity: Editor, door: RequestDoor, services: Services
) -> ProjectView:
    """Store a new project at revision 1, serving no source or repository but the built-in ``hub`` source until it is
    linked. 422 naming the field for a malformed slug or a blank or padded name; 409 when the slug is another
    project's current slug."""
    try:
        record = services.project_authoring.create(
            request.slug, request.name, change_context(identity, door), description=request.description
        )
    except ConfigFieldError as exc:
        raise _unprocessable(exc.field, exc.message) from exc
    except ProjectSlugTaken as exc:
        raise _conflict(exc) from exc
    return _view(record)


@router.get("/projects/{project}", response_model=ProjectView, dependencies=[_VIEW])
def get_project(project: PathProject) -> ProjectView:
    """One project, named by its id, its slug, or a slug it left. 404 when it names none."""
    return _view(project)


@router.patch("/projects/{project}", response_model=ProjectView)
def patch_project(
    project: PathProject,
    request: ProjectPatchRequest,
    identity: Editor,
    door: RequestDoor,
    services: Services,
    if_match: IfMatch = None,
) -> ProjectView:
    """Apply only the fields present; an explicit `null` is refused naming the field. A patch that changes nothing
    writes nothing. A slug the project leaves keeps naming it until another project takes it. 404 unknown; 409 for a
    stale `If-Match` (naming the current revision) or a slug another project holds; 422 naming the field."""
    edit = _edit(request)
    try:
        edited = services.project_authoring.edit(project, edit, change_context(identity, door), if_match=if_match)
    except ConfigFieldError as exc:
        raise _unprocessable(exc.field, exc.message) from exc
    except (ConfigRevisionConflict, ProjectSlugTaken) as exc:
        raise _conflict(exc) from exc
    return _view(edited)


# --- Source links ------------------------------------------------------------------------------------------------


def _work_source(name: str, services: TenantServices) -> ConfiguredWorkSource:
    record = services.work_source_records.get(name)
    if record is None:
        raise _not_found(f"unknown work source {name!r}")
    return record


def _built_in_source_refused(source: str) -> HTTPException:
    return _unprocessable(
        "source", f"work source {source} is built in: every project draws from it", "built_in_source", where="path"
    )


@router.get("/projects/{project}/source-links", response_model=SourceLinksView, dependencies=[_VIEW])
def list_source_links(project: PathProject, services: Services) -> SourceLinksView:
    """The project's standing source links by source name, the built-in ``hub`` source among them. 404 when the
    project is unknown."""
    views = [_source_view(link) for link in services.project_links.sources_of(project.project_id)]
    views.append(_built_in_view(project))
    return SourceLinksView(source_links=sorted(views, key=lambda view: view.name))


@router.put("/projects/{project}/source-links/{source}", response_model=SourceLinkView)
def link_source(
    project: PathProject,
    source: str,
    identity: Editor,
    door: RequestDoor,
    services: Services,
    if_match: IfMatch = None,
) -> SourceLinkView:
    """Link the project to a stored work source, retired or not — a first link, or a relink of one it unlinked; a
    standing link, and the built-in ``hub`` source, are left as they are. 404 for an unknown project or source; 409
    for a stale `If-Match` on a stored link."""
    if is_built_in(source):
        return _built_in_view(project)
    record = _work_source(source, services)
    try:
        link = services.project_authoring.link_source(
            project, record, change_context(identity, door), if_match=if_match
        )
    except ConfigRevisionConflict as exc:
        raise _conflict(exc) from exc
    return _source_view(link)


@router.post("/projects/{project}/source-links/{source}/retire", response_model=SourceLinkView)
def unlink_source(
    project: PathProject,
    source: str,
    identity: Editor,
    door: RequestDoor,
    services: Services,
    if_match: IfMatch = None,
) -> SourceLinkView:
    """Unlink the project from a work source by retiring the link; unlinking an unlinked link changes nothing. 422 for
    the built-in ``hub`` source; 404 for an unknown project or a source it never linked; 409 for a stale
    `If-Match`."""
    if is_built_in(source):
        raise _built_in_source_refused(source)
    link = services.project_links.source_link(project.project_id, source)
    if link is None:
        raise _not_found(f"project {project.slug} has no link to work source {source!r}")
    try:
        unlinked = services.project_authoring.unlink_source(link, change_context(identity, door), if_match=if_match)
    except ConfigRevisionConflict as exc:
        raise _conflict(exc) from exc
    return _source_view(unlinked)


# --- Repository links --------------------------------------------------------------------------------------------


@router.get("/projects/{project}/repository-links", response_model=RepositoryLinksView, dependencies=[_VIEW])
def list_repository_links(project: PathProject, services: Services) -> RepositoryLinksView:
    """The project's standing repository links by repository id. 404 when the project is unknown."""
    links = services.project_links.repositories_of(project.project_id)
    held = services.repository_records.get_many([link.repository_id for link in links])
    return RepositoryLinksView(repository_links=[_repository_view(link, held[link.repository_id]) for link in links])


@router.put("/projects/{project}/repository-links/{repository}", response_model=RepositoryLinkView)
def link_repository(
    identity: Editor,
    project: PathProject,
    repository: PathRepository,
    door: RequestDoor,
    services: Services,
    if_match: IfMatch = None,
) -> RepositoryLinkView:
    """Link the project to a stored repository, retired or not — a first link, or a relink of one it unlinked; a
    standing link is left as it is. 404 for an unknown project or repository; 409 for a stale `If-Match` on a stored
    link."""
    try:
        link = services.project_authoring.link_repository(
            project, repository, change_context(identity, door), if_match=if_match
        )
    except ConfigRevisionConflict as exc:
        raise _conflict(exc) from exc
    return _repository_view(link, repository)


@router.post("/projects/{project}/repository-links/{repository}/retire", response_model=RepositoryLinkView)
def unlink_repository(
    identity: Editor,
    project: PathProject,
    repository: PathRepository,
    door: RequestDoor,
    services: Services,
    if_match: IfMatch = None,
) -> RepositoryLinkView:
    """Unlink the project from a repository by retiring the link; unlinking an unlinked link changes nothing. 404 for
    an unknown project or a repository it never linked; 409 for a stale `If-Match`."""
    link = services.project_links.repository_link(project.project_id, repository.repository_id)
    if link is None:
        raise _not_found(f"project {project.slug} has no link to repository {repository.slug!r}")
    try:
        unlinked = services.project_authoring.unlink_repository(link, change_context(identity, door), if_match=if_match)
    except ConfigRevisionConflict as exc:
        raise _conflict(exc) from exc
    return _repository_view(unlinked, repository)
