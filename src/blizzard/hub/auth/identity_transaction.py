"""The identity unit of work — the identity writes that must commit together, on one transaction.

Each identity repository alone commits every call on its own. A sign-in that claims the hub administrator role, or
admits a person into a tenant, writes several of them and must land whole or not at all:
:meth:`IIdentityTransactions.begin` hands out the write repositories of every global identity table bound to one
transaction, committed when the block exits and rolled back when it raises. The concrete adapter lives at
``src/blizzard/hub/auth/internal/identity_transaction.py`` (``bzh:dependency-inversion``)."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

from blizzard.hub.auth.facts import IWriteAuthFactsRepository
from blizzard.hub.auth.hub_admins import IWriteHubAdminRepository
from blizzard.hub.auth.identities import IWriteIdentityRepository
from blizzard.hub.auth.memberships import IWriteMembershipRepository
from blizzard.hub.auth.sessions import IWriteSessionRepository
from blizzard.hub.auth.superuser_bootstrap import IWriteSuperuserBootstrapRepository
from blizzard.hub.auth.users import IWriteUserRepository


@dataclass(frozen=True)
class IdentityWrites:
    """The identity write repositories one unit of work holds; every read and write through them runs on its
    transaction and sees its earlier writes."""

    users: IWriteUserRepository
    identities: IWriteIdentityRepository
    sessions: IWriteSessionRepository
    memberships: IWriteMembershipRepository
    hub_admins: IWriteHubAdminRepository
    bootstrap: IWriteSuperuserBootstrapRepository
    auth_facts: IWriteAuthFactsRepository


class IIdentityTransactions(Protocol):
    """Opens identity units of work."""

    def begin(self) -> AbstractContextManager[IdentityWrites]:
        """One transaction over every identity write: committed when the block exits, rolled back when it raises."""
        ...
