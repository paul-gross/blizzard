"""The tenant's configured adapters as the hub container holds them: an edited forge record rebuilds its adapter and
the replacement keeps its forge key's budget; one adapter's ports and the bag field it fills are one instance within a
tenant and differ across tenants; and evicting a tenant or shutting the hub down closes the clients the tenant's records
built on the running action loop, while a borrowed adapter and the tracing handle stay open (component tier).

The forge adapters are real ``GitHubLanding``s over a recorded ``httpx2`` transport that notes the thread it closes on."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.platform_tracing.handle import DisabledPlatformTracing
from blizzard.foundation.registry.internal.overrides import OverrideSet
from blizzard.foundation.registry.internal.recording import RecordingRegistry
from blizzard.hub.api.chunk_events import announce_minted
from blizzard.hub.composition import HubCore, HubServices, build_hub_core, build_services, connections_for_engine
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.chunk.model import WorkRef, WorkRefLabel
from blizzard.hub.domain.chunk.ports.commit_links import IReadLandedCommitLinks
from blizzard.hub.domain.config.authoring import ConfigAuthoring
from blizzard.hub.domain.config.credentials import IReadCredentialRepository
from blizzard.hub.domain.config.forges import (
    FORGE_KIND_CAPABILITIES,
    ConfiguredForge,
    ForgeEdit,
    IReadForgeRepository,
    IWriteForgeRepository,
)
from blizzard.hub.domain.config.repositories import IReadRepositoryRecordRepository, RepositoryId
from blizzard.hub.domain.config_apply.service import ConfigApplication
from blizzard.hub.domain.observability.analytics.extraction import shipped_extractors
from blizzard.hub.forge.auth import IForgeAuthorizations
from blizzard.hub.forge.budget import ForgeBudgetLow, ForgeBudgets, ForgePriority
from blizzard.hub.forge.commit_links import LandedCommitLinks
from blizzard.hub.forge.internal.github_landing import GitHubLanding
from blizzard.hub.forge.rates import rate_dialect
from blizzard.hub.forge.readiness import CredentialReadiness
from blizzard.hub.forge.registry import ForgeBinding, ForgeRegistry, WriteLocks
from blizzard.hub.live_config import ConfigObjectCache
from blizzard.hub.store.internal.repository_record_store import RepositoryRecordStore
from blizzard.hub.tenancy import StoreScope
from blizzard.hub.work_sources.source import IWorkSourceRegistry
from tests.support.config_inputs import VERSION_WINDOW, create_hub_app
from tests.support.registry_overrides import owned_ref
from tests.support.scaffolding import (
    DEFAULT_FIXTURE_REPOSITORIES,
    OP,
    TEST_HUB_ACTIONS,
    FakeWorkSource,
    HubHarness,
    WorkSourceRegistry,
    add_tenant,
    build_hub,
    default_tenant,
    hub_store_connections,
    migrate_to,
    seed_repositories,
)

_NOW = datetime(2026, 7, 13, tzinfo=UTC)
_PULL = {
    "number": 7,
    "state": "open",
    "mergeable_state": "clean",
    "head": {"ref": "feat", "sha": "h1"},
    "base": {"ref": "main", "sha": "b1"},
}
_LOOP_THREAD = "hub-action-loop"


class _Transport(httpx2.MockTransport):
    """Answers every pull request read with :data:`_PULL` under ``headers``, recording each request and the thread
    it is closed on."""

    def __init__(self, headers: dict[str, str]) -> None:
        super().__init__(self._answer)
        self.headers = headers
        self.requests: list[httpx2.Request] = []
        self.closed_on: str | None = None

    async def _answer(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(200, headers=self.headers, content=json.dumps(_PULL).encode())

    async def aclose(self) -> None:
        self.closed_on = threading.current_thread().name


def _closed(transport: _Transport) -> str | None:
    """The thread ``transport`` was closed on, waiting a few seconds for a close scheduled onto the loop."""
    deadline = time.monotonic() + 5
    while transport.closed_on is None and time.monotonic() < deadline:
        time.sleep(0.01)
    return transport.closed_on


@dataclass
class _Adapters:
    """Builds every forge adapter over a fresh recorded transport answering under :attr:`headers`."""

    headers: dict[str, str] = field(default_factory=dict[str, str])
    transports: list[_Transport] = field(default_factory=list[_Transport])

    def adapter(self, binding: ForgeBinding) -> GitHubLanding:
        transport = _Transport(dict(self.headers))
        self.transports.append(transport)
        return GitHubLanding(binding, clock=FixedClock(_NOW), transport=transport)

    def forges(
        self,
        records: IReadRepositoryRecordRepository,
        forges: IReadForgeRepository,
        credentials: IReadCredentialRepository,
        objects: ConfigObjectCache,
        authorizations: IForgeAuthorizations,
        budgets: ForgeBudgets,
        write_locks: WriteLocks,
    ) -> ForgeRegistry:
        return ForgeRegistry(
            records=records,
            forges=forges,
            credentials=credentials,
            objects=objects,
            authorizations=authorizations,
            build=self.adapter,
            capabilities=FORGE_KIND_CAPABILITIES,
            budgets=budgets,
            write_locks=write_locks,
            dialects=rate_dialect,
        )


def _repository(hub: HubHarness) -> RepositoryId:
    record = RepositoryRecordStore(hub_store_connections(hub.engine)).resolve("acme-widget")
    assert record is not None
    return record.repository_id


def _forge(hub: HubHarness, repository: RepositoryId) -> ConfiguredForge:
    record = hub.services.repository_records.get(repository)
    assert record is not None
    forge = hub.services.forge_records.get(record.fields.forge_id)
    assert forge is not None
    return forge


def _rate(remaining: int) -> dict[str, str]:
    return {
        "x-ratelimit-limit": "100",
        "x-ratelimit-remaining": str(remaining),
        "x-ratelimit-reset": str(int((_NOW + timedelta(seconds=600)).timestamp())),
    }


def test_editing_a_forge_record_rebuilds_its_adapter_and_the_replacement_keeps_its_budget(tmp_path: Path) -> None:
    adapters = _Adapters(headers=_rate(10))
    hub = build_hub(tmp_path, forges=adapters.forges)
    repository = _repository(hub)
    loop = hub.hub_services.action_loop
    budgets = hub.services.forge_budgets
    loop.start()
    try:
        assert loop.submit(hub.services.forges.changes(repository, priority=ForgePriority.LANDING).change(7)).result(5)
        adapters.headers = {}

        hub.services.config_authoring.edit_forge(
            _forge(hub, repository), ForgeEdit(api_base="http://forge2.fixture"), OP
        )

        with pytest.raises(ForgeBudgetLow):
            loop.submit(hub.services.forges.changes(repository, priority=ForgePriority.SWEEPING).change(7)).result(5)
        first, rebuilt = adapters.transports
        assert rebuilt.requests == []
        assert str(first.requests[0].url).startswith("http://forge.fixture")
        assert hub.services.forge_budgets is budgets
    finally:
        loop.stop()


def _shared(hub: HubHarness, tenant: StoreScope) -> dict[str, list[object]]:
    """Each configured adapter of ``tenant``, as its every port resolves and as its bag field holds it."""
    scope = hub.hub_services.process.tenants.open(tenant).resolver
    services = hub.hub_services.tenants.open_services(tenant)
    objects = hub.hub_services.process.tenants.resolve(tenant, ConfigObjectCache)
    return {
        "forge records": [scope.get(IReadForgeRepository), scope.get(IWriteForgeRepository), services.forge_records],
        "landed commit links": [scope.get(LandedCommitLinks), scope.get(IReadLandedCommitLinks), services.commit_links],
        "forge registry": [scope.get(ForgeRegistry), services.forges],
        "forge budgets": [scope.get(ForgeBudgets), services.forge_budgets],
        "work sources": [scope.get(IWorkSourceRegistry), services.work_sources],
        "credential readiness": [scope.get(CredentialReadiness)],
        "config authoring": [scope.get(ConfigAuthoring), services.config_authoring],
        "config application": [scope.get(ConfigApplication)],
        "config objects": [scope.get(ConfigObjectCache), objects],
    }


def test_one_adapters_aliases_and_bag_field_share_identity_within_a_tenant_and_differ_across_tenants(
    tmp_path: Path,
) -> None:
    hub = build_hub(tmp_path)
    first = StoreScope(default_tenant(hub.engine))
    second = StoreScope(add_tenant(hub.engine, "second"))
    hub.hub_services.tenants.open_tenants.refresh()

    mine, theirs = _shared(hub, first), _shared(hub, second)

    for name, held in (*mine.items(), *theirs.items()):
        assert all(each is held[0] for each in held), name
    for name in mine:
        assert mine[name][0] is not theirs[name][0], name
    scope = hub.hub_services.process.tenants.open(first).resolver
    label: WorkRefLabel = scope.get(WorkRefLabel)
    assert label == hub.services.work_ref_label
    assert label(WorkRef("hub", "w1")) == hub.services.work_sources.label(WorkRef("hub", "w1"))


class _RecordingTracing(DisabledPlatformTracing):
    """The disabled tracing handle, noting any close a container might wrongly make of a borrowed value."""

    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _ClosingSource(FakeWorkSource):
    """A fake work source that notes its close."""

    closed = False

    def close(self) -> None:
        self.closed = True


def _hosted(
    tmp_path: Path, adapters: _Adapters, tracing: _RecordingTracing, borrowed: IWorkSourceRegistry
) -> tuple[HubCore, HubServices, StoreScope]:
    """A hub over a migrated store holding the fixture repository, whose forge registry builds through ``adapters``
    and whose work-source registry is ``borrowed``, supplied as a value; and its one tenant."""
    _, engine = migrate_to(tmp_path, "head")
    seed_repositories(engine, DEFAULT_FIXTURE_REPOSITORIES, tmp_path=tmp_path)

    def overrides(registry: RecordingRegistry) -> OverrideSet:
        replaced = OverrideSet(registry)
        replaced.replace_factory(owned_ref(registry, ForgeRegistry), adapters.forges)
        replaced.replace_value(owned_ref(registry, IWorkSourceRegistry), borrowed)
        return replaced

    core = build_hub_core(
        connections_for_engine(engine),
        clock=FixedClock(_NOW),
        config=HubConfig(root=tmp_path, db_url=str(engine.url)),
        platform_tracing=tracing,
        environ={},
        actions=TEST_HUB_ACTIONS,
        overrides=overrides,
    )
    services = build_services(
        core,
        follow_latest_default=False,
        version_window=VERSION_WINDOW,
        extractors=shipped_extractors(),
        cadences_on=lambda: False,
        announce_minted=announce_minted,
    )
    return core, services, StoreScope(default_tenant(engine))


def _build_adapter(services: HubServices, scope: StoreScope) -> None:
    """Build ``scope``'s adapter for its one repository by reading a change through it on the action loop."""
    tenant = services.tenants.open_services(scope)
    (repository,) = [each.repository_id for each in tenant.repository_records.list_all(include_retired=False)]
    forge = tenant.forges.changes(repository, priority=ForgePriority.LANDING)
    assert services.action_loop.submit(forge.change(7)).result(5).number == 7


def test_eviction_and_shutdown_close_owned_clients_on_the_running_loop_and_leave_borrowed_ones_open(
    tmp_path: Path,
) -> None:
    adapters = _Adapters()
    tracing = _RecordingTracing()
    source = _ClosingSource()
    core, services, scope = _hosted(tmp_path, adapters, tracing, WorkSourceRegistry({"default": source}))
    app = create_hub_app(HubConfig(root=tmp_path, db_url="sqlite://"), services=services)

    with TestClient(app):
        _build_adapter(services, scope)
        # As a tenant's deletion evicts it: its container scope, then its cached services.
        core.process.tenants.evict(scope.tenant)
        core.tenants.evict(scope.tenant)
        assert _closed(adapters.transports[0]) == _LOOP_THREAD

        _build_adapter(services, scope)
        assert adapters.transports[1].closed_on is None

    assert adapters.transports[1].closed_on == _LOOP_THREAD
    assert not source.closed
    assert not tracing.closed
