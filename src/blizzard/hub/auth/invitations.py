"""Invitations — one email address admitted into one tenant with one role, through a link whose token only the
person it is handed to holds.

An invitation is a tenant-owned record; what became of it is one fact beside it (``bzh:facts-not-status``):
``accepted`` with the accepting user, or ``revoked`` with the revoking one. It is live while it has neither and has not
expired, and it is used at most once. The token is minted once, handed back once, and stored only as its sha256. The
concrete adapter lives at ``src/blizzard/hub/store/internal/invitation_store.py`` (``bzh:dependency-inversion``)."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import ClassVar, Protocol

from blizzard.auth_core import Role
from blizzard.foundation.clock import IClock
from blizzard.foundation.hashing import Sha256Hex
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.invitations import MAX_LIFETIME_DAYS, MIN_LIFETIME_DAYS, InvitationState
from blizzard.foundation.roles import domain_model
from blizzard.hub.auth.memberships import MEMBERSHIP_ROLES, MembershipRoleRefused
from blizzard.hub.auth.models import ProviderIdentity
from blizzard.hub.tenancy import TenantId


@domain_model
@dataclass(frozen=True)
class InvitationToken:
    """An invitation link's token in plaintext — handed out once when the invitation is issued, presented when the
    person signs in through the link."""

    #: ``secrets.token_urlsafe`` byte count for a minted token — >= 128 bits.
    BYTES: ClassVar[int] = 32

    plaintext: str

    @classmethod
    def mint(cls) -> InvitationToken:
        return cls(secrets.token_urlsafe(cls.BYTES))

    @property
    def hash(self) -> str:
        """The sha256 hex digest the token is stored as and looked up by."""
        return Sha256Hex(self.plaintext).hex


class InvitationOutcomeKind(StrEnum):
    """The fact that ends an invitation's life before it expires."""

    ACCEPTED = "accepted"
    REVOKED = "revoked"


@domain_model
@dataclass(frozen=True)
class Invitation:
    """One invitation: ``email`` (lowercased) may join ``tenant`` as ``role`` until ``expires_at``."""

    invitation_id: str
    tenant: TenantId
    email: str
    role: Role
    token_hash: str
    #: The issuing user's id.
    created_by: str
    created_at: datetime
    expires_at: datetime

    def admits(self, identity: ProviderIdentity) -> bool:
        """Whether ``identity`` may accept this invitation: its provider vouches for every address it reports as
        verified, and one of them is this invitation's, compared case-insensitively."""
        if not identity.emails_authoritative:
            return False
        return self.email in {address.lower() for address in identity.verified_emails}


@domain_model
@dataclass(frozen=True)
class InvitationOutcome:
    """The one fact recorded about an invitation: who accepted or revoked it, and when."""

    invitation_id: str
    kind: InvitationOutcomeKind
    actor_user_id: str
    recorded_at: datetime


@domain_model
@dataclass(frozen=True)
class InvitationRecord:
    """An invitation and the fact that ended it, if any."""

    invitation: Invitation
    outcome: InvitationOutcome | None

    def state(self, now: datetime) -> InvitationState:
        if self.outcome is not None:
            if self.outcome.kind is InvitationOutcomeKind.ACCEPTED:
                return InvitationState.ACCEPTED
            return InvitationState.REVOKED
        if self.invitation.expires_at <= now:
            return InvitationState.EXPIRED
        return InvitationState.LIVE


@domain_model
@dataclass(frozen=True)
class TenantInvitation:
    """An invitation a link's token resolved to, and the tenant it admits into."""

    tenant: TenantId
    invitation_id: str


class IInvitationTokenReads(Protocol):
    """Resolving an invitation link's token to its invitation before any tenant is known."""

    def invitation_for_token_hash(self, token_hash: str) -> TenantInvitation | None:
        """The invitation whose token hashes to ``token_hash``, or ``None`` when none does."""
        ...


class IReadInvitationRepository(Protocol):
    """One tenant's invitations, read."""

    def get(self, invitation_id: str) -> InvitationRecord | None: ...

    def list(self) -> list[InvitationRecord]:
        """Every invitation of the tenant, oldest first."""
        ...


class IWriteInvitationRepository(IReadInvitationRepository, Protocol):
    """Adds issuing an invitation and recording what became of it."""

    def create(self, invitation: Invitation) -> None: ...

    def record(self, outcome: InvitationOutcome) -> None:
        """Record ``outcome``. An invitation holds at most one, so the first recorded wins: a later one raises
        :class:`InvitationAlreadyResolved` carrying the invitation as it then stands, and writes nothing."""
        ...


class InvitationLifetimeRefused(ValueError):
    """An invitation was asked to live for a number of days outside the allowed range."""

    def __init__(self, days: int) -> None:
        super().__init__(f"an invitation lives between {MIN_LIFETIME_DAYS} and {MAX_LIFETIME_DAYS} days, not {days}")


class InvitationNotFound(LookupError):
    """No invitation of the tenant has this id."""

    def __init__(self, invitation_id: str) -> None:
        super().__init__(f"unknown invitation {invitation_id}")


class InvitationAlreadyResolved(Exception):
    """The invitation was already accepted or revoked."""

    def __init__(self, record: InvitationRecord) -> None:
        assert record.outcome is not None
        super().__init__(f"invitation {record.invitation.invitation_id} was already {record.outcome.kind.value}")


@domain_model
@dataclass(frozen=True)
class IssuedInvitation:
    """A just-issued invitation and its link's plaintext token, returned this once."""

    record: InvitationRecord
    token: InvitationToken


class InvitationService:
    """Issue, list and revoke one tenant's invitations."""

    def __init__(
        self, *, tenant: Callable[[], TenantId], invitations: IWriteInvitationRepository, clock: IClock
    ) -> None:
        #: The tenant the invitations belong to — asked for only when one is issued, so building the service reads
        #: nothing.
        self._tenant = tenant
        self._invitations = invitations
        self._clock = clock

    def issue(self, *, email: str, role: Role, lifetime_days: int, by: str) -> IssuedInvitation:
        """Issue an invitation admitting ``email`` as ``role`` for ``lifetime_days``, by the user ``by``. Raises
        :class:`MembershipRoleRefused` for a role no membership holds and :class:`InvitationLifetimeRefused` for a
        lifetime out of range."""
        if role not in MEMBERSHIP_ROLES:
            raise MembershipRoleRefused(role)
        if not MIN_LIFETIME_DAYS <= lifetime_days <= MAX_LIFETIME_DAYS:
            raise InvitationLifetimeRefused(lifetime_days)
        now = self._clock.now()
        token = InvitationToken.mint()
        invitation = Invitation(
            invitation_id=Id.mint(IdPrefix.INVITATION, self._clock).value,
            tenant=self._tenant(),
            email=email.strip().lower(),
            role=role,
            token_hash=token.hash,
            created_by=by,
            created_at=now,
            expires_at=now + timedelta(days=lifetime_days),
        )
        self._invitations.create(invitation)
        return IssuedInvitation(InvitationRecord(invitation, None), token)

    def list(self) -> list[InvitationRecord]:
        return self._invitations.list()

    def revoke(self, invitation_id: str, *, by: str) -> InvitationRecord:
        """Record that the user ``by`` revoked the invitation. Raises :class:`InvitationNotFound` for an unknown id and
        :class:`InvitationAlreadyResolved` for one already accepted or revoked — also when that outcome was recorded
        between this read and its write."""
        record = self._invitations.get(invitation_id)
        if record is None:
            raise InvitationNotFound(invitation_id)
        if record.outcome is not None:
            raise InvitationAlreadyResolved(record)
        outcome = InvitationOutcome(invitation_id, InvitationOutcomeKind.REVOKED, by, self._clock.now())
        self._invitations.record(outcome)
        return InvitationRecord(record.invitation, outcome)
