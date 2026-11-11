"""Project wire models — the ``/api/projects`` record verbs, a project's source and repository links, and the
bodies of the refusals that name projects.

A project is named on the wire by its id or its slug, never its display name; a response names it by all three as
separate fields."""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict

from blizzard.foundation.project_colors import HexColor, ProjectColor
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.wire.base import WireModel
from blizzard.wire.problem import CommonProblemMembers, ProblemDetails
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
    order, lower landing first — ``null`` when unranked. ``base_branch`` is the link's own integration branch —
    ``null`` when it inherits the repository's — and ``effective_base_branch`` the branch the project's chunks start
    from and land into in the repository: its own, else the repository's ``base_branch``."""

    project_id: str
    repository_id: str
    slug: str
    name: str
    linked: bool
    revision: int
    position: float | None
    base_branch: str | None
    effective_base_branch: str


class RepositoryLinkPatchRequest(BaseModel):
    """A sparse edit of a project's link to a repository: an absent field is unchanged. ``base_branch`` names the
    link's own integration branch — a valid git branch name, never checked against the forge — and an explicit
    ``null`` clears it, so the link inherits the repository's ``base_branch`` again."""

    model_config = ConfigDict(extra="forbid")

    base_branch: str | None = None


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


class ProjectRequiredProblem(
    CommonProblemMembers[Literal["urn:blizzard:problem:project-required"], ProjectRequiredBody]
):
    """A project must be selected; its released detail includes the choices."""

    status: int | None = None


class UnknownProjectDetail(BaseModel):
    """The released validation-shaped entry for an unknown project."""

    loc: list[str]
    msg: str
    type: Literal["unknown_project"]


class UnknownProjectProblem(
    CommonProblemMembers[Literal["urn:blizzard:problem:unknown-project"], list[UnknownProjectDetail]]
):
    """A query names a project the tenant does not hold."""

    status: int | None = None


class SourceNotLinkedBody(BaseModel):
    """The ``detail`` of a ``422``: an ingest drew from a configured ``source`` its ``project`` is not linked to —
    ``project`` is ``null`` when the source is linked to no project, so none could take the ingest."""

    code: Literal["source_not_linked"] = "source_not_linked"
    message: str
    #: The source's key — its id; ``slug`` is its current slug.
    source: str
    slug: str | None = None
    project: ProjectRef | None


UNKNOWN_PROJECT: Final = "urn:blizzard:problem:unknown-project"


PROJECT_REQUIRED: Final = "urn:blizzard:problem:project-required"


PROJECT_NOT_FOUND: Final = "urn:blizzard:problem:project-not-found"


PROJECT_LINK_NOT_FOUND: Final = "urn:blizzard:problem:project-link-not-found"


PROJECT_SLUG_TAKEN: Final = "urn:blizzard:problem:project-slug-taken"


LANDING_ORDER_CONFLICT: Final = "urn:blizzard:problem:landing-order-conflict"


class ProjectNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:project-not-found"]]):
    """A path names a project the tenant does not hold."""


class ProjectLinkNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:project-link-not-found"]]):
    """The project holds no link to the named work source or repository."""


class ProjectSlugTakenProblem(ProblemDetails[Literal["urn:blizzard:problem:project-slug-taken"]]):
    """Another project of the tenant holds the slug."""


class LandingOrderConflictDetail(WireModel):
    code: Literal["landing_order_conflict"] = "landing_order_conflict"
    message: str


class LandingOrderConflictProblem(
    CommonProblemMembers[Literal["urn:blizzard:problem:landing-order-conflict"], LandingOrderConflictDetail]
):
    """Concurrent landing-order writes kept changing the order."""

    status: int | None = None


PROJECT_PROBLEM_TYPES: frozenset[str] = frozenset(
    {
        UNKNOWN_PROJECT,
        PROJECT_REQUIRED,
        PROJECT_NOT_FOUND,
        PROJECT_LINK_NOT_FOUND,
        PROJECT_SLUG_TAKEN,
        LANDING_ORDER_CONFLICT,
    }
)
