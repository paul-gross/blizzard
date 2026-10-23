"""Tenant administration driven through the hub's wired service: a tenant is created whole in one transaction — its
row, the packaged graphs, its ``hub`` sequence, its ``default`` project and its first admin — or not at all; it is
usable, and the hub's in-memory set of open tenants knows it, the moment its creation commits; its default project is
an ordinary project, drawing from the ``hub`` source and renamed like any other; names are labels of 1 to 64
characters with no leading or trailing whitespace; a rename reaches the in-memory set; the list shows every tenant
with its state."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
import structlog

from blizzard.auth_core import Permission, Role
from blizzard.foundation.project_colors import ProjectColor
from blizzard.foundation.store.utc import as_utc
from blizzard.foundation.tenants import TenantState
from blizzard.hub.auth.errors import RepoErrorFactory
from blizzard.hub.auth.internal.identity_transaction import IdentityTransactions
from blizzard.hub.documents import YAML_CODEC
from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.graph.model import IWriteGraphRepository
from blizzard.hub.domain.kernel.hub_source import RESERVED_HUB_SOURCE_NAME
from blizzard.hub.domain.projects.model import Project, ProjectEdit, ProjectSlug
from blizzard.hub.domain.tenancy.administration import (
    TenantAdministration,
    TenantAdminUnknown,
    TenantNameRefused,
)
from blizzard.hub.graph_sync import packaged_minter
from blizzard.hub.graphs import PACKAGED, GraphFile
from blizzard.hub.store import schema
from blizzard.hub.store.errors import HubStoreErrorFactory
from blizzard.hub.store.internal.tenant_admin_store import TenantAdministrationStore
from blizzard.hub.tenancy import HUB_SCOPED_OPTION, StoreScope, TenantId, UnknownTenant
from tests.support import (
    HubHarness,
    build_hub,
    close_tenant,
    default_tenant,
    first_color,
    hub_level_connections,
    seed_user,
)

pytestmark = pytest.mark.component

_PACKAGED_NAMES = sorted(GraphFile(path).doc.name for path in PACKAGED.paths)


class _Interrupted(Exception):
    pass


def _row_counts(hub: HubHarness) -> dict[str, int]:
    with hub.engine.connect() as conn:
        conn = conn.execution_options(**{HUB_SCOPED_OPTION: True})
        return {
            table.name: conn.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()
            for table in (
                schema.tenants,
                schema.graphs,
                schema.work_item_sequence,
                schema.projects,
                schema.project_former_slugs,
                schema.config_changes,
                schema.membership_facts,
            )
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


def _projects_and_changes(hub: HubHarness, tenant: TenantId) -> tuple[list[tuple], list[tuple]]:
    p, c = schema.projects.c, schema.config_changes.c
    with hub.engine.connect() as conn:
        projects = conn.execute(
            sa.select(p.project_id, p.slug, p.name, p.description, p.revision).where(p.tenant_id == tenant)
        ).all()
        changes = conn.execute(
            sa.select(c.record_kind, c.record_key, c.project_id, c.actor, c.door, c.op, c.revision).where(
                c.tenant_id == tenant
            )
        ).all()
    return [tuple(row) for row in projects], [tuple(row) for row in changes]


@pytest.mark.parametrize("door", [Door.API, Door.CLI])
def test_a_created_tenant_holds_its_default_project_created_by_its_creator_through_the_door(
    hub: HubHarness, door: Door
) -> None:
    created = hub.hub_services.tenant_admin.create("acme", admin_user_id=None, by="usr_creator", door=door)

    [project], changes = _projects_and_changes(hub, created.tenant_id)
    project_id = project[0]
    assert project_id.startswith("proj_")
    assert project[1:] == ("default", "Default", "", 1)
    assert changes == [("project", project_id, project_id, "usr_creator", door.value, "create", 1)]


def test_a_created_tenants_default_project_is_stored_with_a_palette_color(hub: HubHarness) -> None:
    created = hub.hub_services.tenant_admin.create("acme", admin_user_id=None, by="usr_creator")

    assert _default_project(hub, created.tenant_id).color in set(ProjectColor)


def _default_project(hub: HubHarness, tenant: TenantId) -> Project:
    project = hub.open_services(tenant).projects.get_by_slug(ProjectSlug.parse("default"))
    assert project is not None
    return project


def test_a_created_tenant_reads_its_default_project_the_moment_its_creation_commits(hub: HubHarness) -> None:
    created = hub.hub_services.tenant_admin.create("acme", admin_user_id=None, by="usr_creator")

    projects = hub.open_services(created.tenant_id).projects.list_all()

    assert [(p.slug, p.name, p.description, p.revision, p.created_at) for p in projects] == [
        ("default", "Default", "", 1, created.created_at)
    ]


def test_two_tenants_each_hold_a_default_project_of_their_own(hub: HubHarness) -> None:
    acme = hub.hub_services.tenant_admin.create("acme", admin_user_id=None, by="usr_creator")
    bravo = hub.hub_services.tenant_admin.create("bravo", admin_user_id=None, by="usr_creator")

    held = {tenant: _default_project(hub, tenant).project_id for tenant in (acme.tenant_id, bravo.tenant_id)}

    assert len(set(held.values())) == 2
    for tenant, project_id in held.items():
        assert [p.project_id for p in hub.open_services(tenant).projects.list_all()] == [project_id]


def test_the_default_project_draws_from_the_hub_source_with_no_stored_link(hub: HubHarness) -> None:
    created = hub.hub_services.tenant_admin.create("acme", admin_user_id=None, by="usr_creator")
    project = _default_project(hub, created.tenant_id)
    links = hub.open_services(created.tenant_id).project_links

    assert links.linked_at(project.project_id, RESERVED_HUB_SOURCE_NAME, hub.clock.now())
    assert links.sources_of(project.project_id) == []
    with hub.engine.connect() as conn:
        stored = conn.execute(
            sa.select(sa.func.count())
            .select_from(schema.project_source_links)
            .where(schema.project_source_links.c.tenant_id == created.tenant_id)
        ).scalar_one()
    assert stored == 0


def test_the_default_project_is_renamed_and_its_slug_moved_like_any_other(hub: HubHarness) -> None:
    created = hub.hub_services.tenant_admin.create("acme", admin_user_id=None, by="usr_creator")
    services = hub.open_services(created.tenant_id)
    project = _default_project(hub, created.tenant_id)

    edited = services.project_authoring.edit(
        project, ProjectEdit(slug="main", name="Main"), ChangeContext(actor="usr_ada", door=Door.API), if_match=1
    )

    assert (edited.project_id, edited.slug, edited.name, edited.revision) == (project.project_id, "main", "Main", 2)
    assert services.projects.get_by_slug(ProjectSlug.parse("default")) is None
    assert services.projects.get_by_former_slug(ProjectSlug.parse("default")) == edited
    assert services.projects.list_all() == [edited]


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
    mint = packaged_minter(hub.clock, YAML_CODEC)
    minted: list[int] = []

    def mint_then_fail(graphs: IWriteGraphRepository) -> None:
        minted.append(len(mint(graphs)))
        raise _Interrupted

    store = hub.hub_services.tenant_admin
    interrupted = TenantAdministration(
        tenants=TenantAdministrationStore(
            hub_level_connections(hub.engine),
            IdentityTransactions(
                hub_level_connections(hub.engine),
                RepoErrorFactory(structlog.get_logger("test")),
                HubStoreErrorFactory(structlog.get_logger("test")),
            ),
        ),
        mint_packaged=mint_then_fail,
        changed=hub.hub_services.tenants.open_tenants.refresh,
        pick=first_color,
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


_MISSHAPEN_NAMES = pytest.mark.parametrize(
    "name",
    ["", "   ", "\n", " acme", "acme ", "acme\t", "x" * 65],
    ids=["blank", "whitespace-only", "a-line-break", "leading", "trailing", "trailing-tab", "65-characters"],
)


@_MISSHAPEN_NAMES
def test_a_name_without_a_tenant_names_shape_is_refused_and_nothing_is_created(hub: HubHarness, name: str) -> None:
    before = _row_counts(hub)

    with pytest.raises(TenantNameRefused):
        hub.hub_services.tenant_admin.create(name, admin_user_id=None, by="usr_creator")

    assert _row_counts(hub) == before
    assert [t.name for t in hub.hub_services.tenant_admin.list_tenants()] == ["default"]


@_MISSHAPEN_NAMES
def test_a_rename_to_a_name_without_a_tenant_names_shape_is_refused_and_renames_nothing(
    hub: HubHarness, name: str
) -> None:
    admin = hub.hub_services.tenant_admin
    created = admin.create("acme", admin_user_id=None, by="usr_creator")

    with pytest.raises(TenantNameRefused):
        admin.rename(created.tenant_id, name)

    assert {t.tenant_id: t.name for t in admin.list_tenants()}[created.tenant_id] == "acme"
    assert hub.hub_services.tenants.open_tenants.name(created.tenant_id) == "acme"


@pytest.mark.parametrize("name", ["x" * 64, "acme corp", "a"], ids=["64-characters", "an-inner-space", "one-character"])
def test_a_name_of_up_to_64_characters_with_inner_whitespace_is_kept_as_given(hub: HubHarness, name: str) -> None:
    admin = hub.hub_services.tenant_admin

    created = admin.create(name, admin_user_id=None, by="usr_creator")
    renamed = admin.rename(created.tenant_id, name)

    assert (created.name, renamed.name) == (name, name)
    assert {t.tenant_id: t.name for t in admin.list_tenants()}[created.tenant_id] == name


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
    close_tenant(hub.engine, created.tenant_id, by="usr_x", at=created.created_at)

    listed = {t.tenant_id: t for t in admin.list_tenants()}

    assert listed[created.tenant_id].state is TenantState.DELETING
    closed_at = listed[created.tenant_id].closed_at
    assert closed_at is not None and as_utc(closed_at) == created.created_at
    assert listed[default_tenant(hub.engine)].closed_at is None
    assert as_utc(listed[created.tenant_id].created_at) == created.created_at
    assert listed[default_tenant(hub.engine)].state is TenantState.OPEN
    with pytest.raises(UnknownTenant):
        admin.rename(created.tenant_id, "acme-again")
    assert hub.hub_services.tenants.open_services(StoreScope(default_tenant(hub.engine))) is not None
