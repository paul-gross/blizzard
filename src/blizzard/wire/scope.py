"""Scope create/edit requests and the read view.

A create names a slug and mints it if unseen, or reads back the existing scope
unchanged; edit changes only the stored description. The lifecycle verbs return an
updated view, the graph lifecycle shape."""

from __future__ import annotations

from pydantic import BaseModel


class ScopeCreateRequest(BaseModel):
    """Mint a scope, or no-op onto the existing one of the same slug."""

    slug: str
    description: str = ""


class ScopeEditRequest(BaseModel):
    """Change a scope's stored description in place."""

    description: str


class ScopeLifecycleRequest(BaseModel):
    """Retire or re-enable a scope — records who flipped it."""

    by: str = "operator"


class ScopeView(BaseModel):
    """A scope as served by the create/list/read/lifecycle routes."""

    slug: str
    description: str
    created_at: str
    retired: bool = False
