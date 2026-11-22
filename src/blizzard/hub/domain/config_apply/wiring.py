"""The config-apply area's registration: the use case behind a declarative apply, over the one configured-record writer
and the reads its entries resolve against. The store-free graph declares it absent."""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.foundation.registry.interface import Lifetime, Registry, port
from blizzard.hub.domain.cadence.ports import IReadCadenceRepository
from blizzard.hub.domain.config.authoring import ConfigAuthoring
from blizzard.hub.domain.config.credentials import IReadCredentialRepository
from blizzard.hub.domain.config.forges import IReadForgeRepository
from blizzard.hub.domain.config.repositories import IReadRepositoryRecordRepository
from blizzard.hub.domain.config.secrets import ISecretCatalog
from blizzard.hub.domain.config.work_sources import IReadWorkSourceRepository
from blizzard.hub.domain.config_apply.service import ConfigApplication
from blizzard.hub.domain.garden.routines import IReadRoutineRepository, IReadRoutineScopeRepository
from blizzard.hub.domain.garden.scopes import IReadScopeRepository
from blizzard.hub.domain.graph.model import IReadGraphRepository
from blizzard.hub.domain.projects.color import ColorPick
from blizzard.hub.domain.projects.links import IReadProjectLinks
from blizzard.hub.domain.projects.model import IReadProjectRepository
from blizzard.hub.domain.projects.order import IReadProjectPositions
from blizzard.hub.domain.urgency.ports import IReadTenantSettings, IReadUrgencyOrder, IReadUrgencyRepository
from blizzard.hub.tenancy import STORE_UNAVAILABLE, StoreScope


def config_application(
    authoring: ConfigAuthoring,
    repositories: IReadRepositoryRecordRepository,
    work_sources: IReadWorkSourceRepository,
    forges: IReadForgeRepository,
    credentials: IReadCredentialRepository,
    projects: IReadProjectRepository,
    links: IReadProjectLinks,
    positions: IReadProjectPositions,
    scopes: IReadScopeRepository,
    routines: IReadRoutineRepository,
    routine_scopes: IReadRoutineScopeRepository,
    cadences: IReadCadenceRepository,
    graphs: IReadGraphRepository,
    secrets: ISecretCatalog,
    scope: StoreScope,
    urgencies: IReadUrgencyRepository,
    urgency_order: IReadUrgencyOrder,
    tenant_settings: IReadTenantSettings,
    pick: ColorPick,
    clock: IClock,
) -> ConfigApplication:
    """The tenant's declarative apply, over the one writer and the reads its entries resolve against."""
    return ConfigApplication(
        authoring=authoring,
        repositories=repositories,
        work_sources=work_sources,
        forges=forges,
        credentials=credentials,
        projects=projects,
        links=links,
        positions=positions,
        scopes=scopes,
        routines=routines,
        routine_scopes=routine_scopes,
        cadences=cadences,
        graphs=graphs,
        secrets=secrets,
        tenant_id=scope.tenant,
        urgencies=urgencies,
        urgency_order=urgency_order,
        tenant_settings=tenant_settings,
        pick=pick,
        clock=clock,
    )


def register(registry: Registry) -> None:
    """Declare the tenant's declarative apply."""
    registry.factory(config_application, lifetime=Lifetime.TENANT)


def register_absent(registry: Registry) -> None:
    """Declare the apply absent, for the store-free graph."""
    registry.absent(port(ConfigApplication), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
