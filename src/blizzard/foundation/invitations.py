"""The invitation vocabulary the hub and its wire share: an invitation's state, and the lifetimes one may be issued
with."""

from __future__ import annotations

from enum import StrEnum

#: The shortest lifetime, in whole days, an invitation may be issued with.
MIN_LIFETIME_DAYS = 1
#: The longest lifetime, in whole days, an invitation may be issued with.
MAX_LIFETIME_DAYS = 30
#: The lifetime, in whole days, of an invitation issued without naming one.
DEFAULT_LIFETIME_DAYS = 7


class InvitationState(StrEnum):
    """What became of an invitation: ``live`` until it is accepted, revoked, or expires, whichever comes first."""

    LIVE = "live"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    REVOKED = "revoked"
