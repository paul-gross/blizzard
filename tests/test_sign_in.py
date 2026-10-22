"""``SignIn`` — whom a provider callback admits (unit tier).

Exercised against a fake of the ``AuthService`` calls it composes and a fake token-resolution port; the real
acceptance rules are pinned in ``tests/test_auth_service.py`` and the route's mapping in
``tests/test_auth_login_api.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.foundation.sign_in_refusal import SignInRefusal
from blizzard.hub.auth.invitations import TenantInvitation
from blizzard.hub.auth.models import ProviderIdentity, Session, User
from blizzard.hub.auth.service import InvitationAccepted
from blizzard.hub.auth.sign_in import PlainSignIn, SignIn
from blizzard.hub.tenancy import TenantClosed, TenantId

pytestmark = pytest.mark.unit

_TENANT = TenantId("ten_one")
_USER = User(
    user_id="usr_1", username="ada", display_name="Ada", email=None, created_at=datetime(2026, 1, 1, tzinfo=UTC)
)
_IDENTITY = ProviderIdentity(subject="42", handle="ada", email=None, email_verified=False)
_INVITATION = TenantInvitation(tenant=_TENANT, invitation_id="inv_1")


class _FakeAuth:
    def __init__(
        self, *, known: User | None = None, accepted: InvitationAccepted | SignInRefusal | None = None
    ) -> None:
        self.known = known
        self.accepted = accepted
        self.closed = False
        self.minted: list[User] = []
        self.linked = 0

    def link_or_mint(self, identity: ProviderIdentity, *, provider_name: str) -> User | None:
        self.linked += 1
        return self.known

    def mint_session(self, user: User) -> tuple[str, Session]:
        self.minted.append(user)
        return "plaintext", None  # type: ignore[return-value]

    def accept_invitation(
        self, identity: ProviderIdentity, *, provider_name: str, invitation: TenantInvitation
    ) -> InvitationAccepted | SignInRefusal:
        if self.closed:
            raise TenantClosed(invitation.tenant)
        assert self.accepted is not None
        return self.accepted


class _FakeInvitations:
    def __init__(self, resolves: TenantInvitation | None) -> None:
        self.resolves = resolves

    def invitation_for_token_hash(self, token_hash: str) -> TenantInvitation | None:
        return self.resolves


def _sign_in(auth: _FakeAuth, resolves: TenantInvitation | None = _INVITATION) -> SignIn:
    return SignIn(auth=auth, invitations=_FakeInvitations(resolves))  # type: ignore[arg-type]


def _admit(sign_in: SignIn, token_hash: str | None) -> InvitationAccepted | PlainSignIn | SignInRefusal:
    return sign_in.admit(_IDENTITY, provider_name="github", invitation_token_hash=token_hash)


def test_a_known_person_signs_in_plainly() -> None:
    auth = _FakeAuth(known=_USER)
    assert _admit(_sign_in(auth), None) == PlainSignIn(_USER, "plaintext")
    assert auth.minted == [_USER]


def test_an_unknown_person_with_no_invitation_is_refused_invite_only() -> None:
    auth = _FakeAuth(known=None)
    assert _admit(_sign_in(auth), None) is SignInRefusal.INVITE_ONLY
    assert auth.minted == []


def test_an_accepted_invitation_admits_into_its_tenant() -> None:
    accepted = InvitationAccepted(_USER, "plaintext", _TENANT)
    auth = _FakeAuth(accepted=accepted)
    assert _admit(_sign_in(auth), "hash") is accepted
    assert auth.linked == 0


def test_an_unresolved_token_is_unusable_and_never_a_plain_sign_in() -> None:
    auth = _FakeAuth(known=_USER)
    assert _admit(_sign_in(auth, resolves=None), "hash") is SignInRefusal.INVITATION_UNUSABLE
    assert auth.linked == 0
    assert auth.minted == []


def test_a_closed_tenant_makes_the_invitation_unusable_and_never_a_plain_sign_in() -> None:
    auth = _FakeAuth(known=_USER)
    auth.closed = True
    assert _admit(_sign_in(auth), "hash") is SignInRefusal.INVITATION_UNUSABLE
    assert auth.linked == 0


def test_a_refused_acceptance_is_carried_and_never_a_plain_sign_in() -> None:
    auth = _FakeAuth(known=_USER, accepted=SignInRefusal.INVITATION_EMAIL_MISMATCH)
    assert _admit(_sign_in(auth), "hash") is SignInRefusal.INVITATION_EMAIL_MISMATCH
    assert auth.linked == 0
    assert auth.minted == []
