"""Invitation routes — issue, list and revoke the request's tenant's invitations.

They act in the request's tenant like any member route, gated on ``USER_MANAGE`` there, and admit the hub
administrator in any tenant without a membership through ``MEMBERSHIP_GRANT_ANY`` — so a tenant with no admin yet can
be given one. They read and write invitations and nothing else of the tenant's. A tenant admin invites by address and
never learns whether that address already has an account: no answer here depends on it."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from blizzard.auth_core import Permission
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import TenantBoundAdmitting, require_either
from blizzard.hub.api.deps import Services
from blizzard.hub.auth.invitations import (
    InvitationAlreadyResolved,
    InvitationNotFound,
    InvitationOutcomeKind,
    InvitationRecord,
)
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.wire.invitation import (
    InvitationCreateRequest,
    InvitationCreateResponse,
    InvitationListResponse,
    InvitationView,
)

router = APIRouter(
    prefix="/api",
    tags=["invitations"],
    dependencies=[
        Depends(reject_runner_principal),
        Depends(TenantBoundAdmitting(Permission.MEMBERSHIP_GRANT_ANY)),
    ],
)

#: ``USER_MANAGE`` in the request's tenant, or the hub administrator's ``MEMBERSHIP_GRANT_ANY``.
_INVITATION_MANAGER = require_either(Permission.USER_MANAGE, Permission.MEMBERSHIP_GRANT_ANY)

Manager = Annotated[ResolvedIdentity, Depends(_INVITATION_MANAGER)]


def invitation_view(record: InvitationRecord, *, now: datetime) -> InvitationView:
    invitation = record.invitation
    outcome = record.outcome
    return InvitationView(
        invitation_id=invitation.invitation_id,
        email=invitation.email,
        role=invitation.role,
        created_by=invitation.created_by,
        created_at=iso_utc(invitation.created_at),
        expires_at=iso_utc(invitation.expires_at),
        state=record.state(now),
        accepted_by=(
            outcome.actor_user_id if outcome is not None and outcome.kind is InvitationOutcomeKind.ACCEPTED else None
        ),
    )


@router.post("/invitations", response_model=InvitationCreateResponse, status_code=status.HTTP_201_CREATED)
def create_invitation(
    request: InvitationCreateRequest, services: Services, identity: Manager
) -> InvitationCreateResponse:
    """Invite ``email`` into the tenant as ``role``. The link's token is returned this once; only its hash is kept."""
    issued = services.invitations.issue(
        email=request.email, role=request.role, lifetime_days=request.expires_in, by=identity.user_id
    )
    return InvitationCreateResponse(
        invitation=invitation_view(issued.record, now=services.clock.now()), token=issued.token.plaintext
    )


@router.get("/invitations", response_model=InvitationListResponse, dependencies=[Depends(_INVITATION_MANAGER)])
def list_invitations(services: Services) -> InvitationListResponse:
    """Every invitation of the tenant with its state — never a link."""
    now = services.clock.now()
    return InvitationListResponse(invitations=[invitation_view(r, now=now) for r in services.invitations.list()])


@router.post(
    "/invitations/{invitation_id}/revocations",
    response_model=InvitationView,
    status_code=status.HTTP_201_CREATED,
)
def revoke_invitation(invitation_id: str, services: Services, identity: Manager) -> InvitationView:
    """Revoke a live or expired invitation: 404 for one the tenant does not hold, 409 for one already accepted or
    revoked."""
    try:
        record = services.invitations.revoke(invitation_id, by=identity.user_id)
    except InvitationNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvitationAlreadyResolved as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return invitation_view(record, now=services.clock.now())
