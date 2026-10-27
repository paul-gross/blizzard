"""The sign-in refusal vocabulary."""

from __future__ import annotations

from enum import StrEnum


class SignInRefusal(StrEnum):
    """Why a sign-in admitted nobody — the code the login page shows its text for. Every refusal writes
    nothing: no user, identity link, membership, invitation fact or session."""

    #: A first sign-in with no invitation, by an identity the hub does not know or whose provider does not vouch.
    INVITE_ONLY = "invite_only"
    #: The invitation is expired, revoked or already used — or names nothing.
    INVITATION_UNUSABLE = "invitation_unusable"
    #: No verified email on the account matches the invitation's; the invitation stays live.
    INVITATION_EMAIL_MISMATCH = "invitation_email_mismatch"
