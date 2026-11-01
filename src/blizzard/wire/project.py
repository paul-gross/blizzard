"""Project wire models — the ``/api/projects`` record verbs, a project's source and repository links, and the
bodies of the refusals that name projects.

A project is named on the wire by its id or its slug, never its display name; a response names it by all three as
separate fields."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict

from blizzard.foundation.project_colors import HexColor, ProjectColor
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.wire.base import WireModel
from blizzard.wire.vocabulary import OPEN


class ProjectView(BaseModel):
    """One project — the ``GET /api/projects`` listing row and every project verb's view."""

    project_id: str
    slug: str
    name: str
    description: str
    #: A palette name this build does not know parses as ``Unrecognized``; a hex colour still parses as one.
    color: Annotated[ProjectColor | Unrecognized, OPEN] | HexColor
    revision: int
    created_at: str


class ProjectsListView(WireModel):
    """Every project of the tenant, by slug — ``GET /api/projects``."""

    projects: list[ProjectView] = []


class ProjectCreateRequest(BaseModel):
    """A new project: a ``slug`` of 1-64 lowercase letters, digits and single inner hyphens, unique among the tenant's
    current slugs. Its display ``name`` is the slug when none is stated. A ``color`` is a palette name or a
    ``#rrggbb``; with none stated, the hub gives the project the tenant's least-used palette color."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str | None = None
    description: str = ""
    color: str | None = None


class ProjectPatchRequest(BaseModel):
    """A sparse edit: an absent field is unchanged, a present one is set, and an explicit ``null`` is refused on every
    field. A slug the project leaves keeps resolving to it until another project takes it."""

    model_config = ConfigDict(extra="forbid")

    slug: str | None = None
    name: str | None = None
    description: str | None = None
    color: str | None = None


class ProjectRef(BaseModel):
    """A project as a refusal names it — its id, current slug and display name."""

    project_id: str
    slug: str
    name: str


class SourceLinkView(WireModel):
    """A project's link to a work source: ``name`` is the source's key — its id, or ``hub`` — and ``slug`` its current
    slug. ``linked`` is false once the link is retired (unlinked). The built-in ``hub`` source is linked to every
    project: it has no stored link, so no ``revision``, and cannot be unlinked."""

    project_id: str
    name: str
    slug: str | None = None
    linked: bool
    revision: int | None = None
    built_in: bool = False


class SourceLinksView(WireModel):
    """A project's standing source links by source key, the built-in ``hub`` source among them —
    ``GET /api/projects/{project}/source-links``."""

    source_links: list[SourceLinkView] = []


class RepositoryLinkView(BaseModel):
    """A project's link to a repository, by the repository's ``repository_id``, with its current ``slug`` and ``name``.
    ``linked`` is false once the link is retired (unlinked). ``position`` is the link's place in the project's landing
    order, lower landing first — ``null`` when unranked."""

    project_id: str
    repository_id: str
    slug: str
    name: str
    linked: bool
    revision: int
    position: float | None


class RepositoryLinksView(WireModel):
    """A project's standing repository links in landing order — ranked ones by position, then the unranked, each tie
    by slug — ``GET /api/projects/{project}/repository-links``."""

    repository_links: list[RepositoryLinkView] = []


class LandingOrderRequest(BaseModel):
    """A project's whole landing order: ``repositories``, each an id or a slug, ranked ``1.0``, ``2.0``, … in the
    list's order; a linked repository the list leaves out becomes unranked."""

    model_config = ConfigDict(extra="forbid")

    repositories: list[str]


class LandingPositionRequest(BaseModel):
    """One move in a project's landing order: ``repository`` to just after ``after_repository``, to the front when
    that is ``null``, or to unranked with ``unranked``. Each repository is an id or a slug."""

    model_config = ConfigDict(extra="forbid")

    repository: str
    after_repository: str | None = None
    unranked: bool = False


class ProjectRequiredBody(WireModel):
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
    #: The source's key — its id; ``slug`` is its current slug.
    source: str
    slug: str | None = None
    project: ProjectRef | None
