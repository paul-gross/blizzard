"""Repository record wire models — the ``/api/repositories`` record verbs. Request models
are ``extra="forbid"``."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class RepositorySummary(BaseModel):
    """One repository — the ``GET /api/repositories`` listing row and every record verb's view. ``repository_id`` is
    what every stored reference holds; ``slug`` is the handle people type, and ``name`` a display label."""

    repository_id: str
    slug: str
    name: str
    forge_api_url: str
    owner: str
    repo: str
    base_branch: str
    secret_name: str
    revision: int
    created_at: str
    created_by: str
    retired: bool = False


class RepositoryDocument(BaseModel):
    """A repository as a document — the create body, and the model whose JSON Schema
    ``GET /api/config/schema/repositories`` serves. ``slug`` names the repository and ``name`` is its display label —
    the slug when omitted on create, left as stored on an edit. ``secret_name`` names a stored secret."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str | None = None
    forge_api_url: str
    owner: str
    repo: str
    base_branch: str
    secret_name: str


class RepositoryPatchRequest(BaseModel):
    """A sparse edit: an absent field is unchanged, a present one is set, and an explicit
    ``null`` is refused on every field. A slug that changes keeps resolving to the repository until another claims
    it."""

    model_config = ConfigDict(extra="forbid")

    slug: str | None = None
    name: str | None = None
    forge_api_url: str | None = None
    owner: str | None = None
    repo: str | None = None
    base_branch: str | None = None
    secret_name: str | None = None


class RepositoriesListView(BaseModel):
    """Every stored repository — ``GET /api/repositories``."""

    repositories: list[RepositorySummary] = []
