"""Repository record wire models — the ``/api/repositories`` record verbs."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from blizzard.wire.base import WireModel
from blizzard.wire.forge import DEPRECATED_FIELD, OldFieldWarning


class LandingPolicyName(StrEnum):
    """A landing policy a write may name — how a repository's changes land."""

    MERGE_ON_CHECKS = "merge-on-checks"
    FAST_FORWARD = "fast-forward"
    PARK = "park"


class RepositorySummary(WireModel):
    """One repository — the ``GET /api/repositories`` listing row and every record verb's view. ``repository_id`` is
    what every stored reference holds; ``slug`` is the handle people type, and ``name`` a display label."""

    repository_id: str
    slug: str
    name: str
    forge_id: str
    forge_slug: str
    forge_name: str
    #: The API origin of the forge the repository names — read ``forge_id`` instead.
    forge_api_url: str = Field(json_schema_extra=DEPRECATED_FIELD)
    owner: str
    repo: str
    base_branch: str
    secret_name: str
    #: How the repository's changes land; a policy this build does not know is passed through as it reads.
    landing_policy: str
    #: How long a parked change may wait, in hours; ``None`` reads as the default.
    park_timeout_hours: int | None = None
    revision: int
    created_at: str
    created_by: str
    created_by_name: str | None = None
    retired: bool = False
    #: One entry per old address field the write that answered this used — empty on every other answer.
    warnings: list[OldFieldWarning] = []


class RepositoryDocument(BaseModel):
    """A repository as a document — an entry of the version-1 configuration document, and the model whose JSON Schema
    ``GET /api/config/schema/repositories`` serves. ``slug`` names the repository and ``name`` is its display label —
    the slug when omitted on create, left as stored on an edit, as is an omitted ``landing_policy`` (a new repository's
    is ``merge-on-checks``). ``secret_name`` names a stored secret."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str | None = None
    forge_api_url: str
    owner: str
    repo: str
    base_branch: str
    secret_name: str
    landing_policy: LandingPolicyName | None = None
    park_timeout_hours: int | None = None


class RepositoryCreateRequest(BaseModel):
    """The ``POST /api/repositories`` body: every field of a repository's document entry, with ``forge`` — a forge's id
    or slug — beside them, naming the forge. ``forge_api_url`` is the old way to name it: alone, it is resolved to the
    tenant's forge with that API origin, made when none holds it; beside ``forge``, it is accepted only when it is that
    forge's API origin. Either way the write is answered with a warning. Naming neither is refused."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str | None = None
    forge: str | None = None
    forge_api_url: str | None = Field(default=None, json_schema_extra=DEPRECATED_FIELD)
    owner: str
    repo: str
    base_branch: str
    secret_name: str
    landing_policy: LandingPolicyName = LandingPolicyName.MERGE_ON_CHECKS
    park_timeout_hours: int | None = None


class RepositoryPatchRequest(BaseModel):
    """A sparse edit: an absent field is unchanged, a present one is set, and an explicit ``null`` is refused on every
    field but ``park_timeout_hours``, where it clears the timeout back to its default. ``forge`` re-points the
    repository at a forge by id or slug; ``forge_api_url`` does so the old way, with a warning. A slug that changes
    keeps resolving to the repository until another claims it."""

    model_config = ConfigDict(extra="forbid")

    slug: str | None = None
    name: str | None = None
    forge: str | None = None
    forge_api_url: str | None = Field(default=None, json_schema_extra=DEPRECATED_FIELD)
    owner: str | None = None
    repo: str | None = None
    base_branch: str | None = None
    secret_name: str | None = None
    landing_policy: LandingPolicyName | None = None
    park_timeout_hours: int | None = None


class RepositoriesListView(WireModel):
    """Every stored repository — ``GET /api/repositories``."""

    repositories: list[RepositorySummary] = []
