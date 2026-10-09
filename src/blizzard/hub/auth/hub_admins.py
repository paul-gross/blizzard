"""Hub administrators — the role above every tenant, recorded as facts (``bzh:facts-not-status``).

Any number of users may hold it; the newest fact per user is in force. It is granted first by the superuser claim
(``bootstrap``) or carried over (``migration``), and afterwards only by a hub administrator, whose user id the fact
names. The last holder is kept: a revocation that would leave the hub without one is refused. The concrete adapter
lives at ``src/blizzard/hub/auth/internal/hub_admin_repository.py`` (``bzh:dependency-inversion``)."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import domain_model
from blizzard.hub.auth.models import User
from blizzard.hub.auth.users import IReadUserRepository

if TYPE_CHECKING:
    from blizzard.hub.auth.identity_transaction import IIdentityTransactions

#: ``set_by`` of the hub administrator the identity migration carried over from the claimed superuser.
SET_BY_MIGRATION = "migration"
#: ``set_by`` of the first hub administrator, written by the superuser claim.
SET_BY_BOOTSTRAP = "bootstrap"


class LastHubAdminKept(Exception):
    """Raised when revoking the hub administrator role from its only holder."""

    def __init__(self, user_id: str) -> None:
        super().__init__(f"{user_id} is the last hub administrator; grant the role to another user first")
        self.user_id = user_id


@domain_model
@dataclass(frozen=True)
class HubAdminFact:
    """One grant (``granted``) or revocation of the hub administrator role. ``set_by`` is the granting hub
    administrator's user id, or one of the ``SET_BY_*`` values."""

    user_id: str
    granted: bool
    set_at: datetime
    set_by: str


class IReadHubAdminRepository(Protocol):
    """Read-only hub-administrator lookups, each answering from the newest fact per user."""

    def is_hub_admin(self, user_id: str) -> bool: ...

    def holders(self) -> list[str]:
        """The user id of every hub administrator, in order."""
        ...


class IWriteHubAdminRepository(IReadHubAdminRepository, Protocol):
    """Adds the hub-administrator write."""

    def record(self, fact: HubAdminFact) -> None:
        """Append ``fact``; it is in force from now on for its user."""
        ...


def revocation_refusal(*, holders: Collection[str], subject: str) -> str | None:
    """Why revoking ``subject`` is refused, or ``None``: the hub always keeps one hub administrator."""
    if set(holders) == {subject}:
        return f"{subject} is the last hub administrator"
    return None


class HubAdministrators:
    """Lists, grants and revokes the hub administrator role, each change in one identity unit of work, so the
    last-holder check and the revocation it guards read and write the same transaction. A change to the standing
    already in force writes nothing."""

    def __init__(
        self,
        *,
        hub_admins: IReadHubAdminRepository,
        users: IReadUserRepository,
        transactions: IIdentityTransactions,
        clock: IClock,
    ) -> None:
        self._hub_admins = hub_admins
        self._users = users
        self._transactions = transactions
        self._clock = clock

    def listing(self) -> list[User]:
        """Every hub administrator, in holder order — two reads however many there are."""
        holders = self._hub_admins.holders()
        users = self._users.get_many(holders)
        return [users[h] for h in holders if h in users]

    def grant(self, subject: User, *, by: str) -> None:
        """Make ``subject`` a hub administrator; ``by`` is the granting hub administrator's user id."""
        with self._transactions.begin() as writes:
            if writes.hub_admins.is_hub_admin(subject.user_id):
                return
            writes.hub_admins.record(HubAdminFact(subject.user_id, True, self._clock.now(), by))

    def revoke(self, subject: User, *, by: str) -> None:
        """Revoke ``subject``'s hub administrator role; raises :class:`LastHubAdminKept` when they hold it alone."""
        with self._transactions.begin() as writes:
            holders = writes.hub_admins.holders()
            if revocation_refusal(holders=holders, subject=subject.user_id) is not None:
                raise LastHubAdminKept(subject.user_id)
            if subject.user_id not in holders:
                return
            writes.hub_admins.record(HubAdminFact(subject.user_id, False, self._clock.now(), by))
