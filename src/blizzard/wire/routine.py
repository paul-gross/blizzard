"""Routine create/edit requests and the read view (issue #389).

A create names the graph its runs execute and a default scope (minted if unseen, D4);
edit changes everything but the name, which is immutable (D7)."""

from __future__ import annotations

from pydantic import BaseModel


class RoutineCreateRequest(BaseModel):
    name: str
    graph_name: str
    default_scope_slug: str
    default_model: list[str] = []
    default_effort: str | None = None


class RoutineEditRequest(BaseModel):
    """``name`` is required and must equal the routine's current one (D7) — the request
    restates it so a caller cannot silently target the wrong routine's edit."""

    name: str
    graph_name: str
    default_scope_slug: str
    default_model: list[str] = []
    default_effort: str | None = None


class RoutineView(BaseModel):
    """A routine as served by the create/list/read/edit routes."""

    routine_id: str
    name: str
    graph_name: str
    default_scope_slug: str
    default_model: list[str] = []
    default_effort: str | None = None
    created_at: str
