"""A runner's credential — its bearer token's hash, a revoked hash, its id at sign-in — resolves across
tenants to the runner and the tenant its row belongs to, and the runner's registration is then read in
that tenant; every credential read is marked hub-scoped, so the tenant statement guard lets it pass
(component tier, over a migrated store holding two tenants)."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine, event

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.api.auth import MachineCredentials, runner_signing_in
from blizzard.hub.domain.runners.host import HostAddition, HostTokenRotation
from blizzard.hub.domain.runners.registration import RunnerAddition, TokenRotation
from blizzard.hub.process_root import HubProcess
from blizzard.hub.store import schema
from blizzard.hub.store.internal.host_registry_store import HostRegistryStore
from blizzard.hub.store.internal.hub_scoped_credentials import TenantHost, TenantRunner
from blizzard.hub.store.internal.hub_scoped_reads import HubScopedReads
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.store.tenant_guard import (
    GUARD_ENVIRONMENT_VARIABLE,
    UnscopedTenantStatement,
    guard_hub_statement,
    install_tenant_guard,
)
from blizzard.hub.tenancy import HUB_SCOPED_OPTION, StoreScope, TenantId
from tests.support.scaffolding import close_tenant, configured_core, default_tenant, migrate_to

_AT = datetime(2026, 10, 8, tzinfo=UTC)
_SECOND = TenantId("ten_SECOND")


@pytest.fixture
def guarded() -> Iterator[None]:
    """The process-wide tenant statement guard switched on, as the suite switches it on, and the
    process left as it was found."""
    before = event.contains(Engine, "before_execute", guard_hub_statement)
    assert install_tenant_guard({GUARD_ENVIRONMENT_VARIABLE: "1"})
    yield
    if not before:
        event.remove(Engine, "before_execute", guard_hub_statement)


def _two_tenants(tmp_path: Path) -> tuple[HubProcess, Engine, TenantId]:
    migrate_to(tmp_path, "head")
    # A plain engine over the migrated file: the one ``migrate_to`` hands out is marked hub-scoped.
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'hub.db'}")
    first = default_tenant(engine)
    with engine.begin() as conn:
        conn.execute(sa.insert(schema.tenants).values(tenant_id=_SECOND, name="second", created_at=_AT, created_by="t"))
    core = configured_core(engine, tmp_path, clock=FixedClock(_AT))
    core.tenants.resolve(StoreScope(first), RunnerRegistryStore).add(
        RunnerAddition("rn_a", "alpha", "hash-a", at=_AT, by="admin")
    )
    core.tenants.resolve(StoreScope(_SECOND), RunnerRegistryStore).add(
        RunnerAddition("rn_b", "bravo", "hash-b", at=_AT, by="admin")
    )
    core.tenants.resolve(StoreScope(first), HostRegistryStore).add(
        HostAddition("hs_a", "alpha", "host-hash-a", at=_AT, by="admin")
    )
    core.tenants.resolve(StoreScope(_SECOND), HostRegistryStore).add(
        HostAddition("hs_b", "bravo", "host-hash-b", at=_AT, by="admin")
    )
    return core, engine, first


def _machine(process: HubProcess) -> MachineCredentials:
    """The credential seams over ``process``: its credential reads, and its tenant containers."""
    return MachineCredentials(process)


def test_a_runner_token_resolves_to_its_own_tenants_runner(tmp_path: Path) -> None:
    core, _, first = _two_tenants(tmp_path)
    credentials = core.resolver.get(HubScopedReads).credentials

    assert credentials.runner_for_token_hash("hash-a") == TenantRunner(first, "rn_a")
    assert credentials.runner_for_token_hash("hash-b") == TenantRunner(_SECOND, "rn_b")
    assert credentials.runner_for_token_hash("hash-never-issued") is None


def test_a_revoked_token_names_the_runner_and_tenant_it_was_issued_in(tmp_path: Path) -> None:
    core, _, first = _two_tenants(tmp_path)
    core.tenants.resolve(StoreScope(first), RunnerRegistryStore).rotate_token(
        TokenRotation("rn_a", "hash-a2", at=_AT, by="admin", expected_lifecycle=0)
    )
    core.tenants.resolve(StoreScope(_SECOND), RunnerRegistryStore).revoke_token(
        "rn_b", at=_AT, by="admin", expected_lifecycle=0
    )
    credentials = core.resolver.get(HubScopedReads).credentials

    assert credentials.revoked_token_runner("hash-a") == TenantRunner(first, "rn_a")
    assert credentials.revoked_token_runner("hash-b") == TenantRunner(_SECOND, "rn_b")
    assert credentials.revoked_token_runner("hash-a2") is None
    assert credentials.runner_for_token_hash("hash-b") is None


def test_a_host_token_resolves_to_its_own_tenants_host_and_never_to_a_runner(tmp_path: Path) -> None:
    core, _, first = _two_tenants(tmp_path)
    credentials = core.resolver.get(HubScopedReads).credentials

    assert credentials.host_for_token_hash("host-hash-a") == TenantHost(first, "hs_a")
    assert credentials.host_for_token_hash("host-hash-b") == TenantHost(_SECOND, "hs_b")
    assert credentials.host_for_token_hash("hash-a") is None
    assert credentials.runner_for_token_hash("host-hash-a") is None


def test_a_revoked_host_token_names_the_host_and_tenant_it_was_issued_in(tmp_path: Path) -> None:
    core, _, first = _two_tenants(tmp_path)
    core.tenants.resolve(StoreScope(first), HostRegistryStore).rotate_token(
        HostTokenRotation("hs_a", "host-hash-a2", at=_AT, by="admin")
    )
    core.tenants.resolve(StoreScope(_SECOND), HostRegistryStore).revoke_token("hs_b", at=_AT, by="admin")
    credentials = core.resolver.get(HubScopedReads).credentials

    assert credentials.revoked_token_host("host-hash-a") == TenantHost(first, "hs_a")
    assert credentials.revoked_token_host("host-hash-b") == TenantHost(_SECOND, "hs_b")
    assert credentials.revoked_token_host("host-hash-a2") is None
    assert credentials.host_for_token_hash("host-hash-b") is None
    assert credentials.revoked_token_runner("host-hash-a") is None


def test_a_host_id_resolves_to_the_tenant_holding_it(tmp_path: Path) -> None:
    core, _, first = _two_tenants(tmp_path)
    credentials = core.resolver.get(HubScopedReads).credentials

    assert credentials.host_by_id("hs_a") == TenantHost(first, "hs_a")
    assert credentials.host_by_id("hs_b") == TenantHost(_SECOND, "hs_b")
    assert credentials.host_by_id("hs_ghost") is None
    assert credentials.runner_by_id("hs_a") is None


def test_a_runner_id_resolves_to_the_tenant_holding_it(tmp_path: Path) -> None:
    core, _, first = _two_tenants(tmp_path)
    credentials = core.resolver.get(HubScopedReads).credentials

    assert credentials.runner_by_id("rn_a") == TenantRunner(first, "rn_a")
    assert credentials.runner_by_id("rn_b") == TenantRunner(_SECOND, "rn_b")
    assert credentials.runner_by_id("rn_ghost") is None


def test_a_sign_in_reads_the_runner_in_its_own_tenant_and_none_in_a_closed_one(tmp_path: Path) -> None:
    core, engine, _ = _two_tenants(tmp_path)
    machine = _machine(core)

    signing_in = runner_signing_in(machine, "rn_b")
    assert signing_in is not None and signing_in.tenant == _SECOND
    assert (signing_in.registration.runner_id, signing_in.registration.name) == ("rn_b", "bravo")

    # Closing a tenant evicts its container scope; an open tenant is answered from memory.
    close_tenant(engine, _SECOND)
    core.tenants.evict(_SECOND)
    assert runner_signing_in(machine, "rn_b") is None
    assert runner_signing_in(machine, "rn_a") is not None


@pytest.mark.usefixtures("guarded")
def test_every_credential_read_is_marked_hub_scoped_and_passes_the_guard(tmp_path: Path) -> None:
    core, engine, first = _two_tenants(tmp_path)
    credentials = core.resolver.get(HubScopedReads).credentials
    seen: list[Mapping[str, Any]] = []

    def record(conn: sa.Connection, clause: Any, multiparams: Any, params: Any, options: Mapping[str, Any]) -> None:
        seen.append(options)

    event.listen(engine, "before_execute", record)
    try:
        resolved = (
            credentials.runner_for_token_hash("hash-b"),
            credentials.revoked_token_runner("hash-b"),
            credentials.runner_by_id("rn_a"),
            credentials.host_for_token_hash("host-hash-b"),
            credentials.revoked_token_host("host-hash-b"),
        )
    finally:
        event.remove(engine, "before_execute", record)

    assert resolved == (
        TenantRunner(_SECOND, "rn_b"),
        None,
        TenantRunner(first, "rn_a"),
        TenantHost(_SECOND, "hs_b"),
        None,
    )
    assert len(seen) == 5
    assert all(options.get(HUB_SCOPED_OPTION) for options in seen)
    # The same read, unmarked, is one the guard refuses.
    with engine.connect() as conn, pytest.raises(UnscopedTenantStatement, match="runner_registrations"):
        conn.execute(sa.select(schema.runner_registrations.c.runner_id))
