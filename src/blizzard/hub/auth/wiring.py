"""The hub identity area's registrations: every identity repository and service, the OAuth provider registry over its
HTTP client, the IdP signing keys, the login throttle and the trusted-proxy set, and each tenant's invitation service.

Every registration is at the process lifetime but the invitation service, which is the tenant's own over its invitation
writer. Configured inputs are read from the boot :class:`HubConfig`: the OAuth providers and the signing keys exist only
under ``oauth`` auth, as the hosted app has always gated them. The HTTP client is a process resource that closes only
the client it opened; a caller-supplied client enters as a borrowed value and is never closed. The store-free graph
declares the whole area absent."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

import httpx2

from blizzard.foundation.clock import IClock
from blizzard.foundation.forwarded import TrustedProxies
from blizzard.foundation.logging import get_logger
from blizzard.foundation.platform_tracing.handle import IInstrumentClient
from blizzard.foundation.registry.interface import Lifetime, Ref, Registry, port
from blizzard.hub.auth.errors import RepoErrorFactory
from blizzard.hub.auth.facts import AuthFactsService
from blizzard.hub.auth.hub_admins import HubAdministrators
from blizzard.hub.auth.identities import IReadIdentityRepository
from blizzard.hub.auth.identity_transaction import IIdentityTransactions
from blizzard.hub.auth.internal.auth_facts_repository import AuthFactsRepository
from blizzard.hub.auth.internal.auth_state_repository import AuthStateRepository
from blizzard.hub.auth.internal.hub_admin_repository import HubAdminRepository
from blizzard.hub.auth.internal.identity_repository import IdentityRepository
from blizzard.hub.auth.internal.identity_transaction import IdentityTransactions
from blizzard.hub.auth.internal.membership_repository import MembershipRepository
from blizzard.hub.auth.internal.session_repository import SessionRepository
from blizzard.hub.auth.internal.superuser_bootstrap_repository import SuperuserBootstrapRepository
from blizzard.hub.auth.internal.user_repository import UserRepository
from blizzard.hub.auth.invitations import InvitationService, IWriteInvitationRepository
from blizzard.hub.auth.memberships import IReadMembershipRepository, TenantMembers
from blizzard.hub.auth.oauth.providers import configured_providers
from blizzard.hub.auth.oauth.registry import IOAuthProviderRegistry
from blizzard.hub.auth.service import AuthService, InvitationWriters
from blizzard.hub.auth.sessions import IReadSessionRepository
from blizzard.hub.auth.sign_in import SignIn
from blizzard.hub.auth.signing import SigningKeyService
from blizzard.hub.auth.throttle import LiveIpThrottle
from blizzard.hub.auth.users import IReadUserRepository
from blizzard.hub.config import AUTH_MODE_OAUTH, HubConfig
from blizzard.hub.layout import SIGNING_KEYS_PARTS
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.tenancy import STORE_UNAVAILABLE, StoreScope

#: The timeout of the HTTP client the hub opens for its OAuth providers.
_OAUTH_TIMEOUT_SECONDS = 15.0


class BorrowedOAuthClient:
    """The OAuth HTTP client a caller supplied, or ``None`` when the hub opens its own; the hub never closes it."""

    def __init__(self, client: httpx2.Client | None) -> None:
        self._client = client

    def lent(self) -> httpx2.Client | None:
        """The lent client, or ``None`` when the hub is to open its own."""
        return self._client


def repo_errors() -> RepoErrorFactory:
    """The identity adapters' one error factory."""
    return RepoErrorFactory(get_logger("blizzard.hub.auth"))


def identity_transactions(connections: HubStoreConnections, errors: RepoErrorFactory) -> IdentityTransactions:
    """The identity unit-of-work opener."""
    return IdentityTransactions(connections, errors, connections.errors)


def auth_facts(repository: AuthFactsRepository, clock: IClock) -> AuthFactsService:
    """The auth/security fact log's recorder."""
    return AuthFactsService(facts=repository, clock=clock)


def auth_service(
    users: UserRepository,
    identities: IdentityRepository,
    sessions: SessionRepository,
    auth_state: AuthStateRepository,
    clock: IClock,
    superuser_bootstrap: SuperuserBootstrapRepository,
    facts: AuthFactsService,
    memberships: MembershipRepository,
    hub_admins: HubAdminRepository,
    transactions: IdentityTransactions,
    invitations: InvitationWriters,
) -> AuthService:
    """Mint, resolve and slide sessions over the identity repositories."""
    return AuthService(
        users=users,
        identities=identities,
        sessions=sessions,
        auth_state=auth_state,
        clock=clock,
        superuser_bootstrap=superuser_bootstrap,
        auth_facts=facts,
        memberships=memberships,
        hub_admins=hub_admins,
        transactions=transactions,
        invitations=invitations,
    )


def tenant_members(
    memberships: MembershipRepository,
    hub_admins: HubAdminRepository,
    users: UserRepository,
    transactions: IdentityTransactions,
    facts: AuthFactsService,
    clock: IClock,
) -> TenantMembers:
    """Change a tenant's memberships in one identity unit of work."""
    return TenantMembers(
        memberships=memberships,
        hub_admins=hub_admins,
        users=users,
        transactions=transactions,
        auth_facts=facts,
        clock=clock,
    )


def hub_administrators(
    hub_admins: HubAdminRepository, users: UserRepository, transactions: IdentityTransactions, clock: IClock
) -> HubAdministrators:
    """List, grant and revoke the hub administrator role."""
    return HubAdministrators(hub_admins=hub_admins, users=users, transactions=transactions, clock=clock)


def open_oauth_client(borrowed: BorrowedOAuthClient, instrument: IInstrumentClient) -> Iterator[httpx2.Client]:
    """The one HTTP client the OAuth providers share, instrumented. Closes only the client it opened."""
    lent = borrowed.lent()
    client = lent if lent is not None else httpx2.Client(timeout=_OAUTH_TIMEOUT_SECONDS)
    try:
        instrument(client)
        yield client
    finally:
        if lent is None:
            client.close()


def oauth_registry(config: HubConfig, client: httpx2.Client) -> IOAuthProviderRegistry:
    """The configured OAuth providers — none outside ``oauth`` auth, where there is no login mechanism to serve."""
    providers = config.auth.oauth_providers if config.auth.mode == AUTH_MODE_OAUTH else ()
    return configured_providers(providers, http_client=client)


def signing_keys(config: HubConfig) -> SigningKeyService | None:
    """The IdP signing-key lifecycle, built only under ``oauth`` auth: any other deployment never touches disk for a
    keypair it will never mint or publish."""
    if config.auth.mode != AUTH_MODE_OAUTH:
        return None
    return SigningKeyService(config.data_dir.joinpath(*SIGNING_KEYS_PARTS))


def login_throttle(clock: IClock, config: HubConfig) -> LiveIpThrottle:
    """The per-IP login throttle, whose limits are read from the live config on each request."""
    if not config.auth.login_throttle.enabled:
        get_logger("blizzard.hub").warning("sign-ins are unthrottled", key="auth.login_throttle.enabled")
    return LiveIpThrottle(clock=clock)


def trusted_proxies(config: HubConfig) -> TrustedProxies:
    """The configured reverse-proxy trust set."""
    return TrustedProxies.parse(config.trusted_proxies)


def invitation_service(scope: StoreScope, invitations: IWriteInvitationRepository, clock: IClock) -> InvitationService:
    """The tenant's invitation service over its invitation writer."""
    return InvitationService(tenant=lambda: scope.tenant, invitations=invitations, clock=clock)


#: The process keys the area registers, for the store-free graph to declare absent.
_PROCESS_KEYS: tuple[Any, ...] = (
    RepoErrorFactory,
    UserRepository,
    IdentityRepository,
    SessionRepository,
    AuthStateRepository,
    SuperuserBootstrapRepository,
    AuthFactsRepository,
    HubAdminRepository,
    MembershipRepository,
    IdentityTransactions,
    AuthFactsService,
    AuthService,
    TenantMembers,
    HubAdministrators,
    SignIn,
    httpx2.Client,
    IOAuthProviderRegistry,
    cast("Any", SigningKeyService | None),
    LiveIpThrottle,
    TrustedProxies,
    IReadUserRepository,
    IReadMembershipRepository,
    IIdentityTransactions,
    IReadSessionRepository,
    IReadIdentityRepository,
)


def register(registry: Registry, *, invitations: Ref[IWriteInvitationRepository]) -> None:
    """Declare the identity area: its repositories and services at the process, and each tenant's invitation service
    over the tenant's ``invitations`` store, which its write port aliases."""
    registry.context(port(BorrowedOAuthClient), lifetime=Lifetime.PROCESS)
    registry.factory(repo_errors, lifetime=Lifetime.PROCESS)
    users = registry.service(UserRepository, lifetime=Lifetime.PROCESS)
    identities = registry.service(IdentityRepository, lifetime=Lifetime.PROCESS)
    sessions = registry.service(SessionRepository, lifetime=Lifetime.PROCESS)
    registry.service(AuthStateRepository, lifetime=Lifetime.PROCESS)
    registry.service(SuperuserBootstrapRepository, lifetime=Lifetime.PROCESS)
    registry.service(AuthFactsRepository, lifetime=Lifetime.PROCESS)
    registry.service(HubAdminRepository, lifetime=Lifetime.PROCESS)
    memberships = registry.service(MembershipRepository, lifetime=Lifetime.PROCESS)
    transactions = registry.factory(identity_transactions, lifetime=Lifetime.PROCESS)
    registry.factory(auth_facts, lifetime=Lifetime.PROCESS)
    auth = registry.factory(auth_service, lifetime=Lifetime.PROCESS)
    registry.factory(tenant_members, lifetime=Lifetime.PROCESS)
    registry.factory(hub_administrators, lifetime=Lifetime.PROCESS)
    registry.service(SignIn, lifetime=Lifetime.PROCESS)
    registry.resource(open_oauth_client, lifetime=Lifetime.PROCESS)
    providers = registry.factory(oauth_registry, lifetime=Lifetime.PROCESS)
    registry.factory(signing_keys, lifetime=Lifetime.PROCESS)
    throttle = registry.factory(login_throttle, lifetime=Lifetime.PROCESS)
    registry.factory(trusted_proxies, lifetime=Lifetime.PROCESS)
    for key, ref in (
        (AuthService, auth),
        (LiveIpThrottle, throttle),
        (IOAuthProviderRegistry, providers),
        (IReadMembershipRepository, memberships),
        (IReadSessionRepository, sessions),
        (IReadUserRepository, users),
    ):
        registry.optional(port(key), ref, lifetime=Lifetime.PROCESS)
    registry.alias(port(IReadUserRepository), users)
    registry.alias(port(IReadMembershipRepository), memberships)
    registry.alias(port(IIdentityTransactions), transactions)
    registry.alias(port(IReadSessionRepository), sessions)
    registry.alias(port(IReadIdentityRepository), identities)
    registry.alias(port(IWriteInvitationRepository), invitations)
    registry.factory(invitation_service, lifetime=Lifetime.TENANT)


def register_absent(registry: Registry) -> None:
    """Declare the identity area absent, for the store-free graph."""
    for missing in _PROCESS_KEYS:
        registry.absent(port(missing), lifetime=Lifetime.PROCESS, label=STORE_UNAVAILABLE)
    for missing in (
        AuthService,
        LiveIpThrottle,
        IOAuthProviderRegistry,
        IReadMembershipRepository,
        IReadSessionRepository,
        IReadUserRepository,
    ):
        registry.optional(port(missing), None, lifetime=Lifetime.PROCESS)
    for missing in (InvitationService, IWriteInvitationRepository):
        registry.absent(port(missing), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
