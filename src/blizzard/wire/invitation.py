"""Invitation wire bodies — issuing, listing and revoking a tenant's invitations
(``src/blizzard/hub/api/invitations.py``).

:class:`InvitationCreateResponse` is the only body that ever carries an invitation's token; the listing never does."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator

from blizzard.auth_core import MEMBERSHIP_ROLES, Role
from blizzard.foundation.invitations import (
    DEFAULT_LIFETIME_DAYS,
    MAX_LIFETIME_DAYS,
    MIN_LIFETIME_DAYS,
    InvitationState,
)


class InvitationCreateRequest(BaseModel):
    """Invite ``email`` into the request's tenant as ``role``, for ``expires_in`` whole days."""

    #: The one address that may accept the invitation; stored lowercased.
    email: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[^@\s]+@[^@\s]+$")]
    #: A membership role: ``guest``, ``contributor`` or ``admin``.
    role: Role
    #: Whole days the invitation stays live.
    expires_in: Annotated[int, Field(strict=True, ge=MIN_LIFETIME_DAYS, le=MAX_LIFETIME_DAYS)] = DEFAULT_LIFETIME_DAYS

    @field_validator("role")
    @classmethod
    def _membership_role(cls, role: Role) -> Role:
        if role not in MEMBERSHIP_ROLES:
            raise ValueError(f"{role.value} is not a membership role")
        return role


class InvitationView(BaseModel):
    """One invitation as its tenant's admins see it — never its token."""

    invitation_id: str
    email: str
    role: Role
    #: The issuing user's id.
    created_by: str
    created_at: str
    expires_at: str
    state: InvitationState
    #: The accepting user's id — set only when ``state`` is ``accepted``.
    accepted_by: str | None = None


class InvitationCreateResponse(BaseModel):
    """The issued invitation and its link's token. ``token`` is visible only here, once — only its sha256 is kept;
    the link handed to the invited person ends ``/invite/<token>``."""

    invitation: InvitationView
    token: str


class InvitationListResponse(BaseModel):
    """Every invitation of the request's tenant, oldest first."""

    invitations: list[InvitationView]
