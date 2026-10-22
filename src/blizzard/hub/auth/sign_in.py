"""Who a provider callback admits — the use case behind the provider-login callback.

A sign-in started from an invitation link accepts that invitation and lands in its tenant; any other sign-in admits a
person the hub already knows. Nobody else is admitted, and an invitation that is refused is never followed by a plain
sign-in. The route only resolves the state entry, calls :meth:`SignIn.admit`, and maps what it answers
(``bzh:domain-orchestration-split``)."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.roles import domain_model
from blizzard.foundation.sign_in_refusal import SignInRefusal
from blizzard.hub.auth.invitations import IInvitationTokenReads
from blizzard.hub.auth.models import ProviderIdentity, User
from blizzard.hub.auth.service import AuthService, InvitationAccepted
from blizzard.hub.tenancy import TenantClosed


@domain_model
@dataclass(frozen=True)
class PlainSignIn:
    """A sign-in of a person the hub already knows: the person and their new session's plaintext id — handed out this
    once."""

    user: User
    session_id: str


class SignIn:
    """Deciding whom a provider identity is admitted as."""

    def __init__(self, *, auth: AuthService, invitations: IInvitationTokenReads) -> None:
        self._auth = auth
        self._invitations = invitations

    def admit(
        self, identity: ProviderIdentity, *, provider_name: str, invitation_token_hash: str | None
    ) -> InvitationAccepted | PlainSignIn | SignInRefusal:
        """``identity`` admitted through the invitation whose token hashes to ``invitation_token_hash`` when the
        sign-in was started with one, otherwise as a plain sign-in; why nobody was admitted when neither holds."""
        if invitation_token_hash is not None:
            return self._accept_invitation(identity, provider_name, invitation_token_hash)
        user = self._auth.link_or_mint(identity, provider_name=provider_name)
        if user is None:
            return SignInRefusal.INVITE_ONLY
        plaintext, _session = self._auth.mint_session(user)
        return PlainSignIn(user, plaintext)

    def _accept_invitation(
        self, identity: ProviderIdentity, provider_name: str, token_hash: str
    ) -> InvitationAccepted | SignInRefusal:
        invitation = self._invitations.invitation_for_token_hash(token_hash)
        if invitation is None:
            return SignInRefusal.INVITATION_UNUSABLE
        try:
            return self._auth.accept_invitation(identity, provider_name=provider_name, invitation=invitation)
        except TenantClosed:
            return SignInRefusal.INVITATION_UNUSABLE
