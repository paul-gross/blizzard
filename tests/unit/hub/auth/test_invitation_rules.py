"""``Invitation.requested``'s value-only refusals and derivations (unit tier)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.auth_core import Role
from blizzard.foundation.invitations import MAX_LIFETIME_DAYS, MIN_LIFETIME_DAYS
from blizzard.hub.auth.invitations import Invitation, InvitationLifetimeRefused
from blizzard.hub.auth.memberships import MEMBERSHIP_ROLES, MembershipRoleRefused
from blizzard.hub.tenancy import TenantId

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _requested(*, role: Role | None = None, lifetime_days: int = 7, email: str = " A@B.Com ") -> Invitation:
    return Invitation.requested(
        invitation_id="inv_1",
        tenant=TenantId("t"),
        email=email,
        role=role if role is not None else next(iter(MEMBERSHIP_ROLES)),
        token_hash="h",
        created_by="usr_1",
        now=_NOW,
        lifetime_days=lifetime_days,
    )


def test_requested_normalizes_email_and_derives_expiry() -> None:
    invitation = _requested(lifetime_days=3)
    assert invitation.email == "a@b.com"
    assert invitation.created_at == _NOW
    assert invitation.expires_at == _NOW + timedelta(days=3)


@pytest.mark.parametrize("days", [MIN_LIFETIME_DAYS, MAX_LIFETIME_DAYS])
def test_requested_accepts_the_lifetime_bounds(days: int) -> None:
    assert _requested(lifetime_days=days).expires_at == _NOW + timedelta(days=days)


@pytest.mark.parametrize("days", [MIN_LIFETIME_DAYS - 1, MAX_LIFETIME_DAYS + 1])
def test_requested_refuses_a_lifetime_outside_the_bounds(days: int) -> None:
    with pytest.raises(InvitationLifetimeRefused):
        _requested(lifetime_days=days)


def test_requested_refuses_a_role_no_membership_holds() -> None:
    outside = next(role for role in Role if role not in MEMBERSHIP_ROLES)
    with pytest.raises(MembershipRoleRefused):
        _requested(role=outside)
