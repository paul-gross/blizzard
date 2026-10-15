"""Permit wire bodies — requesting, moving and reading a project's permits."""

# exemplar: bzh:utc-instants, bzh:frontend-wire-conformist.

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, StringConstraints

from exemplar.foundation.permits import PermitState, PermitVerb

_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class PermitCreateRequest(BaseModel):
    """Request a permit granting ``capability`` to ``holder``."""

    #: The project to request it in, by id or slug; ``None`` names the tenant's only project.
    project: str | None = None
    #: Who the permit is for, by name.
    holder: _Text
    capability: _Text


class PermitRevokeRequest(BaseModel):
    """Revoke a permit, saying why."""

    note: str


class PermitView(BaseModel):
    """One permit, its state, and the verbs legal from it."""

    permit_id: str
    #: The permit's project; always set on a permit.
    project_id: str | None = None
    #: The project's current slug; ``None`` when the hub holds no record of it.
    project_slug: str | None = None
    #: The project's display name; ``None`` when the hub holds no record of it.
    project_name: str | None = None
    holder: str
    capability: str
    state: PermitState
    verbs: list[PermitVerb]
    requested_at: str
    requested_by: str
    #: When the newest grant, suspension, resumption or revocation landed; ``None`` before the first.
    changed_at: str | None = None
    #: Who recorded that newest change; ``None`` before the first.
    changed_by: str | None = None
    #: Why the permit was revoked; ``None`` unless it is.
    revocation_note: str | None = None


class PermitListResponse(BaseModel):
    """One page of permits, newest requested first; ``next_cursor`` is ``None`` on the last."""

    permits: list[PermitView]
    next_cursor: str | None
