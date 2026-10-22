"""The client a hub-IdP sign-in names — a runner or a host — and what it takes to federate a person to it.

One undifferentiated refusal, :class:`UnknownFederationClient`, covers every way a caller can fail to reach a client:
a redirect URI it never registered or reported, and a person holding no membership in its tenant — a hub administrator
included — so probing reveals nothing about which client ids exist. Only past both can a caller tell a retired client
apart, through the record's own :class:`RunnerRetired` / :class:`HostRetired`."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.auth_core import Role
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.runners.host import Host
from blizzard.hub.domain.runners.registration import RunnerRegistration
from blizzard.hub.tenancy import TenantId


class UnknownFederationClient(Exception):
    """A sign-in names a client the caller cannot reach: an unregistered redirect URI, or no membership in the
    client's tenant. The reason is deliberately not carried."""

    def __init__(self) -> None:
        super().__init__("unknown client or unregistered redirect_uri")


@domain_model
@dataclass(frozen=True)
class FederationClient:
    """A runner or host a sign-in names, in the tenant it was added in."""

    tenant: TenantId
    record: RunnerRegistration | Host

    def admit(self, redirect_uri: str) -> None:
        """Refuse a ``redirect_uri`` the client never registered or reported — the open-redirect guard, judged
        before the caller is authenticated, so nobody is bounced to a provider on its behalf."""
        if not self.record.is_federation_target(redirect_uri):
            raise UnknownFederationClient

    def federate(self, redirect_uri: str, *, role: Role | None) -> Role:
        """The role the client's token carries for a person whose membership in its tenant is ``role``. Refuses, in
        order: an unregistered ``redirect_uri``, no membership, then a retired client (past which only a member can
        tell it apart)."""
        self.admit(redirect_uri)
        if role is None:
            raise UnknownFederationClient
        self.record.refuse_federation(redirect_uri)
        return role
