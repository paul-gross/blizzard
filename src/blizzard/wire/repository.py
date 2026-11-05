"""Repository record wire models — the ``/api/repositories`` record verbs."""

from __future__ import annotations

from pydantic import Field

from blizzard.foundation.landing_policy import LandingPolicy
from blizzard.wire.base import WireModel
from blizzard.wire.forge import DEPRECATED_FIELD, MovedFieldsRequest, OldFieldWarning


class RepositorySummary(WireModel):
    """One repository — the ``GET /api/repositories`` listing row and every record verb's view. ``repository_id`` is
    what every stored reference holds; ``slug`` is the handle people type, and ``name`` a display label."""

    repository_id: str
    slug: str
    name: str
    forge_id: str
    forge_slug: str
    forge_name: str
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
    #: Always empty.
    warnings: list[OldFieldWarning] = Field(default=[], json_schema_extra=DEPRECATED_FIELD)


#: The address field a repository's forge now carries.
REPOSITORY_MOVED_FIELDS = ("forge_api_url",)


class RepositoryCreateRequest(MovedFieldsRequest):
    """The ``POST /api/repositories`` body: every field of a repository's document entry, with ``forge`` — a forge's id
    or slug — naming the forge it is on. A body naming no forge is refused."""

    moved_fields = REPOSITORY_MOVED_FIELDS

    slug: str
    name: str | None = None
    forge: str | None = None
    owner: str
    repo: str
    base_branch: str
    secret_name: str
    landing_policy: LandingPolicy = LandingPolicy.MERGE_ON_CHECKS
    park_timeout_hours: int | None = None


class RepositoryPatchRequest(MovedFieldsRequest):
    """A sparse edit: an absent field is unchanged, a present one is set, and an explicit ``null`` is refused on every
    field but ``park_timeout_hours``, where it clears the timeout back to its default. ``forge`` re-points the
    repository at a forge by id or slug. A slug that changes keeps resolving to the repository until another claims
    it."""

    moved_fields = REPOSITORY_MOVED_FIELDS

    slug: str | None = None
    name: str | None = None
    forge: str | None = None
    owner: str | None = None
    repo: str | None = None
    base_branch: str | None = None
    secret_name: str | None = None
    landing_policy: LandingPolicy | None = None
    park_timeout_hours: int | None = None


class RepositoriesListView(WireModel):
    """Every stored repository — ``GET /api/repositories``."""

    repositories: list[RepositorySummary] = []
