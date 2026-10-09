"""Tenant administration driven through the hub's wired service: a tenant is created whole in one transaction — its
row, the packaged graphs, its ``hub`` sequence and its first admin — or not at all; it is usable, and the hub's
in-memory set of open tenants knows it, the moment its creation commits; names are labels; a rename reaches the
in-memory set; the list shows every tenant with its state."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
import structlog

from blizzard.auth_core import Permission, Role
from blizzard.foundation.store.utc import as_utc
from blizzard.hub.auth.errors import RepoErrorFactory
from blizzard.hub.auth.internal.identity_transaction import IdentityTransactions
from blizzard.hub.domain.graph.model import IWriteGraphRepository
from blizzard.hub.domain.tenancy.administration import TenantAdministration, TenantAdminUnknown, TenantState
from blizzard.hub.graph_sync import packaged_minter
from blizzard.hub.graphs import PACKAGED, GraphFile
from blizzard.hub.store import schema
from blizzard.hub.store.internal.tenant_admin_store import TenantAdministrationStore
from blizzard.hub.tenancy import HUB_SCOPED_OPTION, StoreScope, TenantId, UnknownTenant
from tests.support import HubHarness, build_hub, default_tenant, hub_level_connections, seed_user

pytestmark = pytest.mark.component

_PACKAGED_NAMES = sorted(GraphFile(path).doc.name for path in PACKAGED.paths)


class _Interrupted(Exception):
    pass


def _row_counts(hub: HubHarness) -> dict[str, int]:
    with hub.engine.connect() as conn:
        conn = conn.execution_options(**{HUB_SCOPED_OPTION: True})
        return {
            table.name: conn.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()
            for table in (schema.tenants, schema.graphs, schema.work_item_sequence, schema.membership_facts)
        }


def _hub_sequence(hub: HubHarness, tenant: TenantId) -> list[tuple[str, int]]:
    seq = schema.work_item_sequence.c
    with hub.engine.connect() as conn:
        rows = conn.execute(sa.select(seq.source, seq.next_ref).where(seq.tenant_id == tenant)).all()
    return [(row.source, row.next_ref) for row in rows]


def _graph_names(hub: HubHarness, tenant: TenantId) -> list[str]:
    return sorted({g.name for g in hub.open_services(tenant).graphs.list_summaries()})


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    hub = build_hub(tmp_path)
    default_tenant(hub.engine)
    return hub


def test_a_created_tenant_starts_with_the_packaged_graphs_and_its_hub_sequence_at_one(hub: HubHarness) -> None:
    created = hub.hub_services.tenant_admin.create("acme", admin_user_id=None, by="usr_creator")

    assert created.tenant_id.startswith("ten_")
    assert (created.name, created.created_by, created.state) == ("acme", "usr_creator", TenantState.OPEN)
    assert _graph_names(hub, created.tenant_id) == _PACKAGED_NAMES
    assert _hub_sequence(hub, created.tenant_id) == [("hub", 1)]


def test_the_named_admin_is_granted_admin_and_the_creator_nothing(hub: HubHarness) -> None:
    creator = seed_user(hub, username="hal", role=Role.PENDING)
    admin = seed_user(hub, username="ada", role=Role.PENDING)

    named = hub.hub_services.tenant_admin.create("acme", admin_user_id=admin.user_id, by=creator.user_id)
    unnamed = hub.hub_services.tenant_admin.create("bravo", admin_user_id=None, by=creator.user_id)

    memberships = hub.hub_services.memberships
    assert memberships.role_in(named.tenant_id, admin.user_id) is Role.ADMIN
    assert memberships.role_in(named.tenant_id, creator.user_id) is None
    assert memberships.members(unnamed.tenant_id) == []


def test_an_unknown_admin_creates_nothing_though_the_graphs_were_already_minted(hub: HubHarness) -> None:
    before = _row_counts(hub)

    with pytest.raises(TenantAdminUnknown):
        hub.hub_services.tenant_admin.create("acme", admin_user_id="usr_nobody", by="usr_creator")

    assert _row_counts(hub) == before
    assert [t.name for t in hub.hub_services.tenant_admin.list_tenants()] == ["default"]


def test_a_creation_interrupted_after_its_mints_leaves_no_partial_tenant(hub: HubHarness) -> None:
    mint = packaged_minter(hub.clock)
    minted: list[int] = []

    def mint_then_fail(graphs: IWriteGraphRepository) -> None:
        minted.append(len(mint(graphs)))
        raise _Interrupted

    store = hub.hub_services.tenant_admin
    interrupted = TenantAdministration(
        tenants=TenantAdministrationStore(
            hub_level_connections(hub.engine),
            IdentityTransactions(hub_level_connections(hub.engine), RepoErrorFactory(structlog.get_logger("test"))),
        ),
        mint_packaged=mint_then_fail,
        changed=hub.hub_services.tenants.open_tenants.refresh,
        clock=hub.clock,
    )
    before = _row_counts(hub)

    with pytest.raises(_Interrupted):
        interrupted.create("acme", admin_user_id=None, by="usr_creator")

    assert minted == [len(_PACKAGED_NAMES)]
    assert _row_counts(hub) == before
    assert [t.name for t in store.list_tenants()] == ["default"]


def test_names_are_labels_never_refused_for_being_taken(hub: HubHarness) -> None:
    first = hub.hub_services.tenant_admin.create("default", admin_user_id=None, by="usr_creator")
    second = hub.hub_services.tenant_admin.create("default", admin_user_id=None, by="usr_creator")

    listed = hub.hub_services.tenant_admin.list_tenants()
    assert first.tenant_id != second.tenant_id
    assert [t.name for t in listed] == ["default", "default", "default"]
    assert {t.tenant_id for t in listed} == {default_tenant(hub.engine), first.tenant_id, second.tenant_id}


def test_a_created_tenant_is_usable_and_known_in_memory_the_moment_its_creation_commits(hub: HubHarness) -> None:
    open_tenants = hub.hub_services.tenants.open_tenants
    sole = default_tenant(hub.engine)
    me_before = hub.client.get("/api/me").json()
    assert open_tenants.only() == sole

    created = hub.hub_services.tenant_admin.create("acme", admin_user_id=None, by="usr_creator")

    assert open_tenants.only() is None
    assert open_tenants.name(created.tenant_id) == "acme"
    assert hub.client.get("/api/graphs").status_code == 409
    named = hub.client.get("/api/graphs", headers={"X-Blizzard-Tenant": created.tenant_id})
    assert named.status_code == 200
    assert sorted({g["name"] for g in named.json()}) == _PACKAGED_NAMES
    me_after = hub.client.get("/api/me").json()
    assert Permission.FLEET_VIEW.value in me_before["permissions"]
    assert Permission.FLEET_VIEW.value not in me_after["permissions"]


def test_a_rename_reaches_the_in_memory_set_and_refuses_a_tenant_that_is_not_open(hub: HubHarness) -> None:
    admin = hub.hub_services.tenant_admin
    created = admin.create("acme", admin_user_id=None, by="usr_creator")

    renamed = admin.rename(created.tenant_id, "acme-renamed")

    assert (renamed.tenant_id, renamed.name, renamed.state) == (created.tenant_id, "acme-renamed", TenantState.OPEN)
    assert hub.hub_services.tenants.open_tenants.name(created.tenant_id) == "acme-renamed"
    with pytest.raises(UnknownTenant):
        admin.rename(TenantId("ten_00000000000000000000000000"), "nobody")


def test_the_list_shows_a_tenant_whose_deletion_is_underway_as_deleting(hub: HubHarness) -> None:
    admin = hub.hub_services.tenant_admin
    created = admin.create("acme", admin_user_id=None, by="usr_creator")
    with hub.engine.begin() as conn:
        conn.execute(
            sa.insert(schema.tenant_deletions).values(tenant_id=created.tenant_id, name="acme", deleted_by="usr_x")
        )

    listed = {t.tenant_id: t for t in admin.list_tenants()}

    assert listed[created.tenant_id].state is TenantState.DELETING
    assert as_utc(listed[created.tenant_id].created_at) == created.created_at
    assert listed[default_tenant(hub.engine)].state is TenantState.OPEN
    with pytest.raises(UnknownTenant):
        admin.rename(created.tenant_id, "acme-again")
    assert hub.hub_services.tenants.open_services(StoreScope(default_tenant(hub.engine))) is not None
