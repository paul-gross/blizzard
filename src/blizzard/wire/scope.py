"""Scope create/edit requests and the read view.

A create names a slug and mints it if unseen, or reads back the existing scope
unchanged; edit is sparse and changes only the stored description. The lifecycle verbs
return an updated view, the graph lifecycle shape."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ScopeCreateRequest(BaseModel):
    """Mint a scope, or no-op onto the existing one of the same slug."""

    slug: str
    description: str = ""


class ScopeEditRequest(BaseModel):
    """A sparse edit: an absent field is unchanged, a present one is set; ``null`` is
    refused. The slug is immutable, so a body carrying it is refused."""

    model_config = ConfigDict(extra="forbid")

    description: str | None = None


class ScopeLifecycleRequest(BaseModel):
    """Retire or re-enable a scope — ``by`` is recorded on the lifecycle fact; the change
    row's actor is the authenticated caller."""

    by: str = "operator"


class ScopeView(BaseModel):
    """A scope as served by the create/list/read/lifecycle routes."""

    slug: str
    description: str
    created_at: str
    retired: bool = False
    revision: int | None = None
