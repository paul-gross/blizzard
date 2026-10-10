"""Project wire models — the ``/api/projects`` record verbs, a project's source and repository links, and the
bodies of the refusals that name projects. Request models are ``extra="forbid"``.

A project is named on the wire by its id or its slug, never its display name; a response names it by all three as
separate fields."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class ProjectView(BaseModel):
    """One project — the ``GET /api/projects`` listing row and every project verb's view."""

    project_id: str
    slug: str
    name: str
    description: str
    revision: int
    created_at: str


class ProjectsListView(BaseModel):
    """Every project of the tenant, by slug — ``GET /api/projects``."""

    projects: list[ProjectView] = []


class ProjectCreateRequest(BaseModel):
    """A new project: a ``slug`` of 1-64 lowercase letters, digits and single inner hyphens, unique among the tenant's
    current slugs, and a display ``name``."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str
    description: str = ""


class ProjectPatchRequest(BaseModel):
    """A sparse edit: an absent field is unchanged, a present one is set, and an explicit ``null`` is refused on every
    field. A slug the project leaves keeps resolving to it until another project takes it."""

    model_config = ConfigDict(extra="forbid")

    slug: str | None = None
    name: str | None = None
    description: str | None = None


class ProjectRef(BaseModel):
    """A project as a refusal names it — its id, current slug and display name."""

    project_id: str
    slug: str
    name: str


class SourceLinkView(BaseModel):
    """A project's link to a work source, named ``name``. ``linked`` is false once the link is retired (unlinked).
    The built-in ``hub`` source is linked to every project: it has no stored link, so no ``revision``, and cannot be
    unlinked."""

    project_id: str
    name: str
    linked: bool
    revision: int | None = None
    built_in: bool = False


class SourceLinksView(BaseModel):
    """A project's standing source links by source name, the built-in ``hub`` source among them —
    ``GET /api/projects/{project}/source-links``."""

    source_links: list[SourceLinkView] = []


class RepositoryLinkView(BaseModel):
    """A project's link to a repository, named ``name``. ``linked`` is false once the link is retired (unlinked)."""

    project_id: str
    name: str
    linked: bool
    revision: int


class RepositoryLinksView(BaseModel):
    """A project's standing repository links by repository name — ``GET /api/projects/{project}/repository-links``."""

    repository_links: list[RepositoryLinkView] = []


class ProjectRequiredBody(BaseModel):
    """The ``detail`` of a ``422``: the call needs a project, named none, and the tenant does not hold exactly one —
    or the project could not be inferred. ``projects`` lists the ones to choose from."""

    code: Literal["project_required"] = "project_required"
    message: str
    projects: list[ProjectRef]


class SourceNotLinkedBody(BaseModel):
    """The ``detail`` of a ``422``: an ingest drew from a configured ``source`` its ``project`` is not linked to —
    ``project`` is ``null`` when the source is linked to no project, so none could take the ingest."""

    code: Literal["source_not_linked"] = "source_not_linked"
    message: str
    source: str
    project: ProjectRef | None
