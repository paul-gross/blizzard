"""The forge area's registrations: the process's per-kind credential checks, and each tenant's forge seams.

Budgets and installation tokens are held per tenant beside the adapter cache, so a rebuilt adapter keeps the budget it
spends and the token its predecessor minted. The registry instruments each adapter's client through the process's
tracing hook. The store-free graph declares each tenant registration absent, from the same roster."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from blizzard.foundation.clock import IClock, IMonotonicClock
from blizzard.foundation.platform_tracing.handle import IInstrumentClient
from blizzard.foundation.registry.interface import Lifetime, Registry, port
from blizzard.hub.delivery.action_loop import ActionLoop
from blizzard.hub.domain.chunk.event_log import EventLogService
from blizzard.hub.domain.chunk.ports.commit_links import IReadLandedCommitLinks
from blizzard.hub.domain.config.credentials import IReadCredentialRepository
from blizzard.hub.domain.config.forges import IReadForgeRepository
from blizzard.hub.domain.config.repositories import IReadRepositoryRecordRepository
from blizzard.hub.domain.config.secrets import ISecretReader
from blizzard.hub.domain.config.work_sources import IReadWorkSourceRepository
from blizzard.hub.domain.projects.links import IReadProjectLinks
from blizzard.hub.forge.auth import IForgeAuthorizations
from blizzard.hub.forge.budget import ForgeBudgets
from blizzard.hub.forge.commit_check import COMMIT_CHECK_SECONDS, SeamCommitResolver
from blizzard.hub.forge.commit_links import LandedCommitLinks
from blizzard.hub.forge.internal.factory import (
    build_credential_probes,
    build_forge_authorizations,
    build_forge_registry,
    build_installation_tokens,
)
from blizzard.hub.forge.internal.github_auth import InstallationTokens
from blizzard.hub.forge.rates import rate_dialect
from blizzard.hub.forge.readiness import CheckBudget, CredentialReadiness, ICredentialProbe
from blizzard.hub.forge.registry import ForgeRegistry, WriteLocks
from blizzard.hub.live_config import ConfigObjectCache
from blizzard.hub.tenancy import STORE_UNAVAILABLE


def register_process(registry: Registry) -> None:
    """Declare the credential probes, by forge kind."""
    registry.factory(build_credential_probes, lifetime=Lifetime.PROCESS)


def forge_authorizations(
    secrets: ISecretReader, forges: IReadForgeRepository, tokens: InstallationTokens
) -> IForgeAuthorizations:
    """The tenant's authorization boundary, the one holder of its secret reader."""
    return build_forge_authorizations(secrets, forges=forges, tokens=tokens)


def forge_budgets(clock: IClock) -> ForgeBudgets:
    """The tenant's rate budgets, one per forge key, read in each forge kind's rate dialect."""
    return ForgeBudgets(clock=clock, dialects=rate_dialect)


def forge_registry(
    records: IReadRepositoryRecordRepository,
    forges: IReadForgeRepository,
    credentials: IReadCredentialRepository,
    objects: ConfigObjectCache,
    authorizations: IForgeAuthorizations,
    clock: IClock,
    budgets: ForgeBudgets,
    write_locks: WriteLocks,
    instrument: IInstrumentClient,
) -> ForgeRegistry:
    """The tenant's forge registry with every forge kind's adapter, each cached by its records' revisions."""
    return build_forge_registry(
        records=records,
        forges=forges,
        credentials=credentials,
        objects=objects,
        authorizations=authorizations,
        clock=clock,
        budgets=budgets,
        write_locks=write_locks,
        instrument=instrument,
    )


def commit_resolver(
    repositories: IReadRepositoryRecordRepository,
    forge_records: IReadForgeRepository,
    links: IReadProjectLinks,
    forges: ForgeRegistry,
    loop: ActionLoop,
) -> SeamCommitResolver:
    """A project's resolver of a cited commit, run on the action loop and waited on for the default bound."""
    return SeamCommitResolver(
        repositories=repositories,
        forge_records=forge_records,
        links=links,
        forges=forges,
        loop=loop,
        timeout=COMMIT_CHECK_SECONDS,
    )


def credential_readiness(
    repositories: IReadRepositoryRecordRepository,
    work_sources: IReadWorkSourceRepository,
    forges: IReadForgeRepository,
    authorizations: IForgeAuthorizations,
    budgets: ForgeBudgets,
    probes: Mapping[str, ICredentialProbe],
    event_log: EventLogService,
    clock: IClock,
    monotonic: IMonotonicClock,
) -> CredentialReadiness:
    """The tenant's read-only credential check, within the default check budget."""
    return CredentialReadiness(
        repositories=repositories,
        work_sources=work_sources,
        forges=forges,
        authorizations=authorizations,
        budgets=budgets,
        probes=probes,
        event_log=event_log,
        clock=clock,
        monotonic=monotonic,
        budget=CheckBudget(),
    )


def register_tenant(registry: Registry) -> None:
    """Declare the tenant's forge seams, and the landed-commit read port over the one links instance."""
    registry.factory(build_installation_tokens, lifetime=Lifetime.TENANT)
    registry.factory(forge_authorizations, lifetime=Lifetime.TENANT)
    registry.factory(forge_budgets, lifetime=Lifetime.TENANT)
    registry.service(WriteLocks, lifetime=Lifetime.TENANT)
    registry.factory(forge_registry, lifetime=Lifetime.TENANT)
    registry.factory(commit_resolver, lifetime=Lifetime.TENANT)
    links = registry.service(LandedCommitLinks, lifetime=Lifetime.TENANT)
    registry.alias(port(IReadLandedCommitLinks), links)
    registry.factory(credential_readiness, lifetime=Lifetime.TENANT)


#: Every key :func:`register_tenant` declares, for the store-free graph to declare absent.
_TENANT_KEYS: tuple[type[Any], ...] = (
    InstallationTokens,
    IForgeAuthorizations,
    ForgeBudgets,
    WriteLocks,
    ForgeRegistry,
    SeamCommitResolver,
    LandedCommitLinks,
    IReadLandedCommitLinks,
    CredentialReadiness,
)


def register_tenant_absent(registry: Registry) -> None:
    """Declare the tenant's forge seams absent, for the store-free graph."""
    for missing in _TENANT_KEYS:
        registry.absent(port(missing), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
