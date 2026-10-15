"""``PermitStore`` over a migrated sqlite-on-disk store (component tier)."""

# exemplar: bzh:tenant-scoped-store, bzh:bulk-reconstitution. The revision sits outside the hub's chain, so
# ``_migrated`` applies it over a store the real chain migrated to its parent; a production test migrates to ``head``.

from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Engine

from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.tenancy import TenantId
from exemplar.foundation.permits import PermitState
from exemplar.hub.domain.permits.model import Permit, PermitAlreadyRequested
from exemplar.hub.store.internal.permit_store import PermitStore
from tests.support import (
    add_tenant,
    count_queries,
    default_project,
    default_tenant,
    migrate_to,
    scoped_connections,
    seed_project,
)

pytestmark = pytest.mark.component

_PERMITS = importlib.import_module("exemplar.hub.store.migrations.versions.20261015_0900_permits")
_T0 = datetime(2026, 3, 1, tzinfo=UTC)


def _migrated(tmp_path: Path) -> Engine:
    _, engine = migrate_to(tmp_path, _PERMITS.down_revision)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        _PERMITS.upgrade()
    return engine


def _project_of(engine: Engine, tenant: TenantId) -> ProjectId:
    return ProjectId(default_project(engine, tenant))


def _permit(n: int, project: ProjectId, *, holder: str = "ada", at: datetime | None = None) -> Permit:
    return Permit.request(
        f"pm_{n:03d}",
        project_id=project,
        holder=holder,
        capability=f"cap-{n}",
        by="op",
        at=at if at is not None else _T0 + timedelta(minutes=n),
    )


def _drain(store: PermitStore, *, project: ProjectId, holder: str, limit: int) -> list[str]:
    seen: list[str] = []
    cursor: str | None = None
    while True:
        page = store.list_page(project=project, holder=holder, limit=limit, cursor=cursor)
        seen.extend(p.permit_id for p in page.permits)
        if page.next_cursor is None:
            return seen
        cursor = page.next_cursor


def _store(engine: Engine, tenant: TenantId) -> PermitStore:
    return PermitStore(scoped_connections(engine, tenant))


def test_a_tenant_reads_only_its_own_permits(tmp_path: Path) -> None:
    engine = _migrated(tmp_path)
    first = default_tenant(engine)
    second = add_tenant(engine, "other")
    _store(engine, first).create(_permit(1, _project_of(engine, first)))
    _store(engine, second).create(_permit(2, _project_of(engine, second)))

    assert _store(engine, first).get("pm_002") is None
    page = _store(engine, second).list_page(project=None, holder=None, limit=10, cursor=None)
    assert [p.permit_id for p in page.permits] == ["pm_002"]


def test_a_duplicate_request_raises_already_requested(tmp_path: Path) -> None:
    engine = _migrated(tmp_path)
    tenant = default_tenant(engine)
    store = _store(engine, tenant)
    store.create(_permit(1, _project_of(engine, tenant)))
    duplicate = Permit.request(
        "pm_dup", project_id=_project_of(engine, tenant), holder="ada", capability="cap-1", by="op", at=_T0
    )

    with pytest.raises(PermitAlreadyRequested):
        store.create(duplicate)

    assert store.get("pm_dup") is None


def test_a_locked_write_lands_as_the_newest_fact(tmp_path: Path) -> None:
    engine = _migrated(tmp_path)
    tenant = default_tenant(engine)
    store = _store(engine, tenant)
    store.create(_permit(1, _project_of(engine, tenant)))

    with store.locked("pm_001") as held:
        store.record_fact_locked(held, held.permit().grant(by="op", at=_T0 + timedelta(days=1)))

    granted = store.get("pm_001")
    assert granted is not None
    assert granted.state() is PermitState.GRANTED


def test_list_page_walks_one_holder_in_one_project_newest_first_ties_by_id_descending(tmp_path: Path) -> None:
    """Three permits share one ``requested_at`` across a page boundary; another project's and another holder's
    permits are left out."""
    engine = _migrated(tmp_path)
    tenant = default_tenant(engine)
    store = _store(engine, tenant)
    project = _project_of(engine, tenant)
    other = seed_project(engine, "other", tenant=tenant)
    tied = _T0 + timedelta(days=1)
    for permit in (
        _permit(1, project),
        _permit(2, project, at=tied),
        _permit(3, project, at=tied),
        _permit(4, project, at=tied),
        _permit(5, project, at=tied, holder="grace"),
        _permit(6, other, at=tied),
    ):
        store.create(permit)

    assert _drain(store, project=project, holder="ada", limit=2) == ["pm_004", "pm_003", "pm_002", "pm_001"]


@pytest.mark.parametrize("size", [3, 30])
def test_list_page_issues_the_same_statements_at_any_size(tmp_path: Path, size: int) -> None:
    engine = _migrated(tmp_path)
    tenant = default_tenant(engine)
    store = _store(engine, tenant)
    project = _project_of(engine, tenant)
    for n in range(size):
        store.create(_permit(n, project))

    assert count_queries(engine, lambda: store.list_page(project=project, holder=None, limit=50, cursor=None)) == 2


@pytest.mark.parametrize("size", [3, 30])
def test_get_many_issues_the_same_statements_at_any_size(tmp_path: Path, size: int) -> None:
    engine = _migrated(tmp_path)
    tenant = default_tenant(engine)
    store = _store(engine, tenant)
    project = _project_of(engine, tenant)
    for n in range(size):
        store.create(_permit(n, project))

    assert count_queries(engine, lambda: store.get_many([f"pm_{n:03d}" for n in range(size)])) == 2
