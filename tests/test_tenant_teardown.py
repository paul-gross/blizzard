"""Tenant deletion driven through the hub's wired teardown: a close records the deletion fact and drops what the hub
holds for the tenant at once — its people answer 404, its runners' tokens 401, its writes are refused — the drain
removes every row of the tenant children first in bounded batches and nothing of another tenant, the completion
removes the tenant itself and records the count, and the ``tenant_teardown`` sweep finishes a drain a budget cut off.
A tenant holding what a service test creates deletes in well under a second, measured here."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi import Request
from fastapi.testclient import TestClient

from blizzard.auth_core import Role
from blizzard.foundation.store.utc import as_utc
from blizzard.foundation.tenants import TenantDeletionState
from blizzard.hub.api.auth_session import TenantRequest, resolve_tenant
from blizzard.hub.api.hub_level import Hub
from blizzard.hub.config import AUTH_MODE_OAUTH
from blizzard.hub.domain.tenancy import teardown
from blizzard.hub.domain.tenancy.teardown import (
    CloseOutcome,
    LastTenant,
    TenantAlreadyDeleted,
    TenantTeardown,
    TenantTeardownSweep,
)
from blizzard.hub.store import schema
from blizzard.hub.store.internal.tenant_teardown_store import DRAIN_ORDER, TenantTeardownStore
from blizzard.hub.store.scoped import is_tenant_owned
from blizzard.hub.tenancy import HUB_SCOPED_OPTION, TenantClosed, TenantId, UnknownTenant
from tests.support import (
    HubHarness,
    added_runner,
    build_hub,
    capture_statements,
    default_project,
    default_tenant,
    hub_level_connections,
    hub_scoped,
    ingest,
    scoped_connections,
    seed_hub_admin,
    seed_membership,
    seed_runner,
    seed_session,
    seed_user,
)

pytestmark = pytest.mark.component

#: Chunks the measured tenant holds — the handful a service test ingests.
_CHUNKS = 8

#: A bound only a pathological drain exceeds; the measurement itself is reported.
_LOOSE_BOUND_SECONDS = 5.0


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    default_tenant(hub.engine)
    return hub


def _created(hub: HubHarness, name: str = "acme") -> TenantId:
    """A tenant created as a hub administrator creates one — its packaged graphs and ``hub`` sequence minted."""
    return hub.hub_services.tenant_admin.create(name, admin_user_id=None, by="usr_creator").tenant_id


def _rows_of(hub: HubHarness, tenant: TenantId) -> dict[str, int]:
    """The tenant's rows in each tenant-owned table, and in ``tenants`` and ``membership_facts``."""
    tables = [t for t in schema.metadata.sorted_tables if is_tenant_owned(t)]
    tables += [schema.tenants, schema.membership_facts]
    with hub.engine.connect() as conn:
        conn = conn.execution_options(**{HUB_SCOPED_OPTION: True})
        counts = {
            t.name: conn.execute(sa.select(sa.func.count()).select_from(t).where(t.c.tenant_id == tenant)).scalar_one()
            for t in tables
        }
    return {name: n for name, n in counts.items() if n}


def _deletions(hub: HubHarness) -> list[sa.Row]:
    with hub.engine.connect() as conn:
        return list(conn.execute(sa.select(schema.tenant_deletions)).all())


def _hub_events(hub: HubHarness) -> list[tuple[str, dict | None]]:  # type: ignore[type-arg]
    return [(e.kind, e.detail) for e in reversed(hub.hub_services.hub_events.recent(50)) if e.kind.startswith("tenant")]


def _small_teardown(hub: HubHarness, *, batch_rows: int) -> tuple[TenantTeardown, TenantTeardownStore]:
    store = TenantTeardownStore(hub_level_connections(hub.engine))
    services = hub.hub_services
    torn = TenantTeardown(
        teardowns=store,
        closed=lambda _tenant: services.tenants.open_tenants.refresh(),
        events=services.hub_event_log,
        clock=services.clock,
        batch_rows=batch_rows,
    )
    return torn, store


def test_the_drain_order_is_every_tenant_owned_table_children_before_parents() -> None:
    owned = [t for t in schema.metadata.sorted_tables if is_tenant_owned(t)]
    position = {t.name: i for i, t in enumerate(DRAIN_ORDER)}

    assert sorted(position) == sorted(t.name for t in owned)
    for table in DRAIN_ORDER:
        for fk in table.foreign_keys:
            parent = fk.column.table
            if parent is not table and parent.name in position:
                assert position[table.name] < position[parent.name], (table.name, parent.name)


def test_a_close_records_the_fact_and_drops_the_tenant_from_memory(hub: HubHarness) -> None:
    tenant = _created(hub)
    assert hub.open_services(tenant) is hub.open_services(tenant)

    outcome = hub.hub_services.tenant_teardown.close(tenant, by="usr_closer")

    assert outcome is CloseOutcome.CLOSED
    [fact] = _deletions(hub)
    assert (fact.tenant_id, fact.name, fact.deleted_by, fact.deleted_at, fact.rows_removed) == (
        tenant,
        "acme",
        "usr_closer",
        None,
        0,
    )
    assert as_utc(fact.closed_at) == hub.hub_services.clock.now()
    assert tenant not in hub.hub_services.tenants.open_tenants.every()
    with pytest.raises(TenantClosed):
        hub.open_services(tenant)
    with pytest.raises(TenantClosed), scoped_connections(hub.engine, tenant).write("test"):
        pass
    assert _hub_events(hub) == [("tenant-closed", {"tenant_id": tenant, "by": "usr_closer"})]


def test_after_a_close_the_tenants_people_get_404_and_its_runners_401(hub: HubHarness) -> None:
    tenant = _created(hub)
    member = seed_user(hub, username="mia", role=Role.PENDING)
    seed_membership(hub.engine, tenant, member.user_id, Role.ADMIN)
    session = seed_session(hub, member)
    token = seed_runner(hub.open_services(tenant), "rn-acme-1", register=False)
    assert hub.app is not None
    client = TestClient(hub.app)
    person = {"Authorization": f"Bearer {session}", "X-Blizzard-Tenant": tenant}
    runner = {"Authorization": f"Bearer {token}"}
    registration = {"workspace_id": "w1", "name": "r-acme"}
    assert client.get("/api/chunks", headers=person).status_code == 200
    assert client.post("/api/fleet/runners", json=registration, headers=runner).status_code == 201

    hub.hub_services.tenant_teardown.close(tenant, by="usr_closer")

    assert client.get("/api/chunks", headers=person).status_code == 404
    assert client.post("/api/fleet/runners", json=registration, headers=runner).status_code == 401


def _closing_after_resolution(hub: HubHarness, tenant: TenantId, rows: dict[str, dict[str, int]]) -> None:
    """Make each request close ``tenant`` right after resolving it, before its route writes — the window a close
    committing mid-request lands in — keeping the tenant's rows as the close left them in ``rows["closed"]``."""
    assert hub.app is not None

    def resolving_then_closing(request: Request, services: Hub) -> TenantRequest:
        resolved = resolve_tenant(request, services)
        services.tenant_teardown.close(tenant, by="usr_closer")
        rows["closed"] = _rows_of(hub, tenant)
        return resolved

    hub.app.dependency_overrides[resolve_tenant] = resolving_then_closing


def test_a_member_write_whose_tenant_closes_mid_request_answers_404_and_writes_nothing(hub: HubHarness) -> None:
    tenant = _created(hub)
    member = seed_user(hub, username="mia", role=Role.PENDING)
    seed_membership(hub.engine, tenant, member.user_id, Role.ADMIN)
    person = {"Authorization": f"Bearer {seed_session(hub, member)}", "X-Blizzard-Tenant": tenant}
    rows: dict[str, dict[str, int]] = {}
    _closing_after_resolution(hub, tenant, rows)
    assert hub.app is not None

    response = TestClient(hub.app).post("/api/secrets", json={"name": "gh", "value": "v"}, headers=person)

    assert (response.status_code, response.json()) == (404, {"detail": "no such tenant"})
    assert _rows_of(hub, tenant) == rows["closed"]


def test_a_runner_write_whose_tenant_closes_mid_request_answers_401_and_writes_nothing(hub: HubHarness) -> None:
    tenant = _created(hub)
    runner = {"Authorization": f"Bearer {seed_runner(hub.open_services(tenant), 'rn-acme-1', register=False)}"}
    rows: dict[str, dict[str, int]] = {}
    _closing_after_resolution(hub, tenant, rows)
    assert hub.app is not None

    response = TestClient(hub.app).post(
        "/api/fleet/runners", json={"workspace_id": "w1", "name": "r-acme"}, headers=runner
    )

    assert (response.status_code, response.json()) == (
        401,
        {"detail": "bearer token does not resolve to a known runner"},
    )
    assert _rows_of(hub, tenant) == rows["closed"]


def _deleting_after_resolution(hub: HubHarness, tenant: TenantId, states: list[TenantDeletionState]) -> None:
    """Make each request delete ``tenant`` outright right after resolving it, before its route writes — the window a
    deletion completing mid-request lands in — keeping each deletion's state in ``states``."""
    assert hub.app is not None

    def resolving_then_deleting(request: Request, services: Hub) -> TenantRequest:
        resolved = resolve_tenant(request, services)
        states.append(services.tenant_teardown.delete(tenant, by="usr_deleter").state)
        return resolved

    hub.app.dependency_overrides[resolve_tenant] = resolving_then_deleting


def _hub_administrator(hub: HubHarness, tenant: TenantId) -> dict[str, str]:
    """The headers of a hub administrator holding no membership, acting in ``tenant``."""
    admin = seed_user(hub, username="hal", role=Role.PENDING)
    seed_hub_admin(hub.engine, admin.user_id)
    return {"Authorization": f"Bearer {seed_session(hub, admin)}", "X-Blizzard-Tenant": tenant}


def test_a_hub_administrators_grant_whose_tenant_is_deleted_mid_request_answers_404_and_records_nothing(
    hub: HubHarness,
) -> None:
    tenant = _created(hub)
    nobody = seed_user(hub, username="nobody", role=Role.PENDING)
    headers = _hub_administrator(hub, tenant)
    states: list[TenantDeletionState] = []
    _deleting_after_resolution(hub, tenant, states)
    assert hub.app is not None

    response = TestClient(hub.app).put(f"/api/members/{nobody.user_id}", json={"role": "admin"}, headers=headers)

    assert (response.status_code, response.json()) == (404, {"detail": "no such tenant"})
    assert states == [TenantDeletionState.DELETED]
    assert _rows_of(hub, tenant) == {}


def test_a_hub_administrators_grant_whose_tenant_closes_mid_request_answers_404_and_records_nothing(
    hub: HubHarness,
) -> None:
    tenant = _created(hub)
    nobody = seed_user(hub, username="nobody", role=Role.PENDING)
    headers = _hub_administrator(hub, tenant)
    rows: dict[str, dict[str, int]] = {}
    _closing_after_resolution(hub, tenant, rows)
    assert hub.app is not None

    response = TestClient(hub.app).put(f"/api/members/{nobody.user_id}", json={"role": "admin"}, headers=headers)

    assert (response.status_code, response.json()) == (404, {"detail": "no such tenant"})
    assert _rows_of(hub, tenant) == rows["closed"]


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [("DELETE", "/api/members/{user_id}", None), ("POST", "/api/users/{user_id}/role", {"role": "pending"})],
    ids=["revoke", "role-alias"],
)
def test_a_tenant_admins_revoke_whose_tenant_closes_mid_request_answers_404_and_records_nothing(
    hub: HubHarness, method: str, path: str, body: dict[str, str] | None
) -> None:
    tenant = _created(hub)
    admin = seed_user(hub, username="mia", role=Role.PENDING)
    seed_membership(hub.engine, tenant, admin.user_id, Role.ADMIN)
    bob = seed_user(hub, username="bob", role=Role.PENDING)
    seed_membership(hub.engine, tenant, bob.user_id, Role.CONTRIBUTOR)
    headers = {"Authorization": f"Bearer {seed_session(hub, admin)}", "X-Blizzard-Tenant": tenant}
    rows: dict[str, dict[str, int]] = {}
    _closing_after_resolution(hub, tenant, rows)
    assert hub.app is not None

    response = TestClient(hub.app).request(method, path.format(user_id=bob.user_id), json=body, headers=headers)

    assert (response.status_code, response.json()) == (404, {"detail": "no such tenant"})
    assert _rows_of(hub, tenant) == rows["closed"]


def test_after_a_close_a_member_signing_in_to_its_runner_is_answered_as_an_unknown_client(hub: HubHarness) -> None:
    tenant = _created(hub)
    member = seed_user(hub, username="mia", role=Role.PENDING)
    seed_membership(hub.engine, tenant, member.user_id, Role.ADMIN)
    services = hub.open_services(tenant)
    seed_runner(services, "rn-acme-1", register=False)
    callback = "https://rn-acme-1.example/api/auth/callback"
    services.fleet.register(
        added_runner(services, "rn-acme-1"), "w1", public_url="https://rn-acme-1.example", redirect_uris=(callback,)
    )
    assert hub.app is not None
    client = TestClient(hub.app)

    def sign_in(runner: str) -> tuple[int, str]:
        resp = client.get(
            "/api/auth/authorize",
            params={"client": runner, "redirect_uri": callback, "state": "s"},
            headers={"Cookie": f"bz_session={seed_session(hub, member)}"},
            follow_redirects=False,
        )
        return resp.status_code, resp.text

    assert sign_in("rn-acme-1")[0] == 200

    hub.hub_services.tenant_teardown.close(tenant, by="usr_closer")

    unknown = sign_in("no-such-runner")
    assert unknown[0] == 400
    assert sign_in("rn-acme-1") == unknown


def test_a_delete_removes_every_row_of_the_tenant_and_nothing_of_another(hub: HubHarness) -> None:
    tenant = _created(hub)
    other = _created(hub, "bystander")
    member = seed_user(hub, username="mia", role=Role.PENDING)
    seed_membership(hub.engine, tenant, member.user_id, Role.ADMIN)
    seed_runner(hub.open_services(tenant), "rn-acme-1")
    before = _rows_of(hub, tenant)
    others_before = _rows_of(hub, other)
    carried_before = _rows_of(hub, default_tenant(hub.engine))

    progress = hub.hub_services.tenant_teardown.delete(tenant, by="usr_closer")

    assert progress.state is TenantDeletionState.DELETED
    assert progress.rows_removed == sum(before.values())
    assert _rows_of(hub, tenant) == {}
    assert _rows_of(hub, other) == others_before
    assert _rows_of(hub, default_tenant(hub.engine)) == carried_before
    [fact] = _deletions(hub)
    assert fact.deleted_at is not None and fact.rows_removed == progress.rows_removed
    assert [kind for kind, _ in _hub_events(hub)] == ["tenant-closed", "tenant-deleted"]
    assert _hub_events(hub)[-1][1] == {"tenant_id": tenant, "rows_removed": progress.rows_removed}


_PROJECT_TABLES = (
    "projects",
    "project_former_slugs",
    "project_source_links",
    "project_source_link_facts",
    "project_repository_links",
    "project_repository_link_facts",
)


def test_a_delete_removes_the_tenants_projects_their_former_slugs_and_their_links(hub: HubHarness) -> None:
    tenant = _created(hub)
    project = default_project(hub.engine, tenant)
    at = datetime(2026, 1, 1, tzinfo=UTC)
    t = {"tenant_id": tenant}
    linked = {**t, "project_id": project}
    made = {"revision": 1, "created_at": at, "created_by": "usr_creator"}
    fact = {"linked": True, "set_at": at, "set_by": "usr_creator"}
    rows = [
        (
            schema.work_sources,
            {
                **t,
                **made,
                "work_source_id": "wsrc_01KXCCC100ZG1665XA1SDASTE5",
                "slug": "github",
                "name": "github",
                "provider": "github",
                "locator": "o/r",
                "annotate": False,
            },
        ),
        (
            schema.repositories,
            {
                **t,
                **made,
                "repository_id": "repo_01KXCCC100ZG1665XA1SDASTE4",
                "slug": "blizzard",
                "name": "blizzard",
                "forge_api_url": "u",
                "owner": "o",
                "repo": "r",
                "base_branch": "main",
            }
            | {"secret_name": "token"},
        ),
        (schema.project_former_slugs, {**linked, "slug": "old", "left_at": at}),
        (schema.project_source_links, {**linked, "work_source_id": "wsrc_01KXCCC100ZG1665XA1SDASTE5", "revision": 1}),
        (schema.project_source_link_facts, {**linked, **fact, "work_source_id": "wsrc_01KXCCC100ZG1665XA1SDASTE5"}),
        (
            schema.project_repository_links,
            {**linked, "repository_id": "repo_01KXCCC100ZG1665XA1SDASTE4", "revision": 1},
        ),
        (schema.project_repository_link_facts, {**linked, **fact, "repository_id": "repo_01KXCCC100ZG1665XA1SDASTE4"}),
    ]
    with hub.engine.begin() as conn:
        for table, values in rows:
            conn.execute(hub_scoped(sa.insert(table)).values(values))
    assert set(_PROJECT_TABLES) <= set(_rows_of(hub, tenant))

    hub.hub_services.tenant_teardown.delete(tenant, by="usr_closer")

    assert _rows_of(hub, tenant) == {}


def test_a_budget_cut_short_leaves_the_deletion_underway_for_the_sweep_to_finish(hub: HubHarness) -> None:
    tenant = _created(hub)
    total = sum(_rows_of(hub, tenant).values())
    torn, store = _small_teardown(hub, batch_rows=2)

    progress = torn.delete(tenant, by="usr_closer", budget=3)

    assert (progress.state, progress.rows_removed) == (TenantDeletionState.DELETING, None)
    assert sum(_rows_of(hub, tenant).values()) == total - 3
    assert store.underway() == [tenant]
    sweep = TenantTeardownSweep(teardown=torn, teardowns=store, budget=4)
    sweep.sweep()
    assert sum(_rows_of(hub, tenant).values()) == total - 7
    while store.underway():
        sweep.sweep()
    assert _rows_of(hub, tenant) == {}
    deletion = store.deletion(tenant)
    assert deletion is not None and deletion.deleted_at is not None and deletion.rows_removed == total


def test_each_drain_transaction_removes_at_most_its_batch(hub: HubHarness) -> None:
    tenant = _created(hub)
    _, store = _small_teardown(hub, batch_rows=5)
    store.close(tenant, by="usr_closer", at=hub.hub_services.clock.now())

    removed = [store.drain_batch(tenant, 5) for _ in range(3)]

    assert removed == [5, 5, 5]
    deletion = store.deletion(tenant)
    assert deletion is not None and deletion.rows_removed == 15


def test_a_repeated_delete_changes_nothing_while_underway_and_is_gone_after(
    hub: HubHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = _created(hub)
    torn = hub.hub_services.tenant_teardown
    monkeypatch.setattr(teardown, "DRAIN_PASS_ROWS", 1)
    assert torn.delete(tenant, by="usr_closer").state is TenantDeletionState.DELETING
    rows = _rows_of(hub, tenant)

    again = torn.delete(tenant, by="usr_other")

    assert (again.state, again.rows_removed) == (TenantDeletionState.DELETING, None)
    assert _rows_of(hub, tenant) == rows
    monkeypatch.undo()
    hub.hub_services.tenant_teardown_sweep.sweep()
    with pytest.raises(TenantAlreadyDeleted) as gone:
        torn.delete(tenant, by="usr_other")
    assert gone.value.deletion.deleted_by == "usr_closer"
    assert [kind for kind, _ in _hub_events(hub)] == ["tenant-closed", "tenant-deleted"]


def test_the_only_open_tenant_and_an_unknown_one_are_refused_and_nothing_is_written(hub: HubHarness) -> None:
    torn = hub.hub_services.tenant_teardown

    with pytest.raises(LastTenant):
        torn.delete(default_tenant(hub.engine), by="usr_closer")
    with pytest.raises(UnknownTenant):
        torn.delete(TenantId("ten_00000000000000000000000000"), by="usr_closer")

    assert _deletions(hub) == []
    assert _hub_events(hub) == []


def test_a_close_locks_every_tenant_row_in_id_order_before_its_last_tenant_check(hub: HubHarness) -> None:
    tenants = sorted([default_tenant(hub.engine), _created(hub, "acme"), _created(hub, "bravo")])
    store = TenantTeardownStore(hub_level_connections(hub.engine))

    with capture_statements(hub.engine) as statements:
        store.close(tenants[1], by="usr_closer", at=hub.hub_services.clock.now())

    texts = [" ".join(sql.split()) for sql, _ in statements]
    locks = [i for i, sql in enumerate(texts) if sql.startswith("UPDATE tenants ")]
    check = next(i for i, sql in enumerate(texts) if sql.startswith("SELECT EXISTS") and "tenant_deletions" in sql)
    assert [tuple(statements[i][1]) for i in locks] == [(tenant,) for tenant in tenants]
    assert max(locks) < check


def test_the_last_open_tenant_stays_refused_while_another_deletion_is_underway(
    hub: HubHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = _created(hub)
    monkeypatch.setattr(teardown, "DRAIN_PASS_ROWS", 1)
    hub.hub_services.tenant_teardown.delete(tenant, by="usr_closer")

    with pytest.raises(LastTenant):
        hub.hub_services.tenant_teardown.delete(default_tenant(hub.engine), by="usr_closer")


def test_a_deletion_never_touches_a_written_export_file(hub: HubHarness, tmp_path: Path) -> None:
    tenant = _created(hub)
    written = tmp_path / "egress" / "steps" / "part-0001.ndjson"
    written.parent.mkdir(parents=True)
    written.write_text(f'{{"tenant_id": "{tenant}"}}\n')

    hub.hub_services.tenant_teardown.delete(tenant, by="usr_closer")

    assert written.read_text() == f'{{"tenant_id": "{tenant}"}}\n'
    [fact] = _deletions(hub)
    assert fact.tenant_id == tenant


def test_a_service_sized_tenant_tears_down_well_under_a_second(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    measured = default_tenant(hub.engine)
    for n in range(_CHUNKS):
        ingest(hub, [{"source": "default", "ref": str(n)}])
    hub.hub_services.tenant_admin.create("survivor", admin_user_id=None, by="usr_creator")

    started = time.perf_counter()
    progress = hub.hub_services.tenant_teardown.delete(measured, by="usr_closer")
    elapsed = time.perf_counter() - started

    print(f"\ntenant teardown: {progress.rows_removed} rows in {elapsed * 1000:.1f} ms")
    assert progress.state is TenantDeletionState.DELETED
    assert progress.rows_removed is not None and progress.rows_removed > _CHUNKS
    assert elapsed < _LOOSE_BOUND_SECONDS
